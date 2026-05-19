"""
LUNA AI Engine - RabbitMQ Consumer
Listens for trend search tasks and data pipeline tasks from NestJS backend.
"""

import json
import asyncio
import logging
import aio_pika

from src.config.settings import settings
from src.message_queue.constants import MQ_EXCHANGES, MQ_QUEUES, MQ_ROUTING_KEYS, MQ_MAX_RETRIES
from src.services.trend_agent import TrendAgent

logger = logging.getLogger(__name__)


class MQConsumer:
    """RabbitMQ consumer for processing async tasks from NestJS."""

    def __init__(self, trend_agent: TrendAgent):
        self.trend_agent = trend_agent
        self._connection = None
        self._channel = None

    async def connect(self):
        """Establish connection to RabbitMQ."""
        try:
            self._connection = await aio_pika.connect_robust(settings.rabbitmq_url)
            self._channel = await self._connection.channel()
            await self._channel.set_qos(prefetch_count=5)

            # Declare exchanges
            search_exchange = await self._channel.declare_exchange(
                MQ_EXCHANGES["SEARCH"], aio_pika.ExchangeType.TOPIC, durable=True,
            )
            dlq_exchange = await self._channel.declare_exchange(
                MQ_EXCHANGES["DLQ"], aio_pika.ExchangeType.TOPIC, durable=True,
            )

            # Declare queues
            trend_tasks_queue = await self._channel.declare_queue(
                MQ_QUEUES["TREND_SEARCH_TASKS"],
                durable=True,
                arguments={
                    "x-dead-letter-exchange": MQ_EXCHANGES["DLQ"],
                    "x-dead-letter-routing-key": "dead.letter",
                },
            )
            await trend_tasks_queue.bind(search_exchange, MQ_ROUTING_KEYS["TREND_TASK"])

            # Dead Letter Queue
            dlq_queue = await self._channel.declare_queue(
                MQ_QUEUES["DEAD_LETTER"], durable=True,
            )
            await dlq_queue.bind(dlq_exchange, "dead.letter")

            logger.info("✅ MQ Consumer connected and queues declared")
            return trend_tasks_queue

        except Exception as e:
            logger.error(f"❌ Failed to connect to RabbitMQ: {e}")
            raise

    async def start_consuming(self):
        """Start consuming messages from all subscribed queues."""
        trend_queue = await self.connect()

        # Start consuming trend search tasks
        await trend_queue.consume(self._handle_trend_task)
        logger.info(f"📡 Listening on queue: {MQ_QUEUES['TREND_SEARCH_TASKS']}")

    async def _handle_trend_task(self, message: aio_pika.IncomingMessage):
        """Handle a trend search task message."""
        async with message.process(requeue=False):
            try:
                body = json.loads(message.body.decode())
                query = body.get("query", "")
                location = body.get("location", "")
                user_id = body.get("user_id", "")
                session_id = body.get("session_id", "")

                logger.info(f"📨 Received trend task: '{query}' in {location}")

                # Execute trend search (sync method — run in thread to avoid blocking event loop)
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, self.trend_agent.search, query, location
                )

                # Publish results back (serialize TrendResult)
                results_data = result.to_dict().get("results", [])
                await self._publish_trend_results(
                    user_id=user_id,
                    session_id=session_id,
                    query=query,
                    location=location,
                    results=results_data,
                )

                logger.info(f"✅ Trend task completed: {len(results_data)} results")

            except Exception as e:
                retry_count = message.headers.get("x-retry-count", 0) if message.headers else 0
                if retry_count < MQ_MAX_RETRIES:
                    logger.warning(f"⚠️ Trend task failed (retry {retry_count + 1}): {e}")
                    # Re-publish with retry count
                    await self._retry_message(message, retry_count + 1)
                else:
                    logger.error(f"❌ Trend task failed after {MQ_MAX_RETRIES} retries: {e}")
                    # Message goes to DLQ automatically

    async def _publish_trend_results(self, user_id: str, session_id: str,
                                      query: str, location: str, results: list):
        """Publish trend search results back to NestJS via RabbitMQ."""
        exchange = await self._channel.declare_exchange(
            MQ_EXCHANGES["SEARCH"], aio_pika.ExchangeType.TOPIC, durable=True,
        )

        message_body = json.dumps({
            "user_id": user_id,
            "session_id": session_id,
            "query": query,
            "location": location,
            "results": results,
        }, ensure_ascii=False)

        await exchange.publish(
            aio_pika.Message(
                body=message_body.encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=MQ_ROUTING_KEYS["TREND_RESULT"],
        )
        logger.info(f"📤 Published trend results for {location}")

    async def _retry_message(self, original_message: aio_pika.IncomingMessage, retry_count: int):
        """Re-publish message with incremented retry count."""
        exchange = await self._channel.declare_exchange(
            MQ_EXCHANGES["SEARCH"], aio_pika.ExchangeType.TOPIC, durable=True,
        )

        headers = dict(original_message.headers) if original_message.headers else {}
        headers["x-retry-count"] = retry_count

        await exchange.publish(
            aio_pika.Message(
                body=original_message.body,
                headers=headers,
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=MQ_ROUTING_KEYS["TREND_TASK"],
        )

    async def close(self):
        """Close MQ connection."""
        if self._connection:
            await self._connection.close()
            logger.info("MQ Consumer connection closed")
