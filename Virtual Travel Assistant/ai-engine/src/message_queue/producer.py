"""
LUNA AI Engine - RabbitMQ Producer
Publishes messages to NestJS backend (e.g., trend results, alerts).
"""

import json
import logging
import aio_pika

from src.config.settings import settings
from src.message_queue.constants import MQ_EXCHANGES, MQ_ROUTING_KEYS

logger = logging.getLogger(__name__)


class MQProducer:
    """RabbitMQ producer for publishing messages from AI Engine."""

    def __init__(self):
        self._connection = None
        self._channel = None

    async def connect(self):
        """Establish connection to RabbitMQ."""
        try:
            self._connection = await aio_pika.connect_robust(settings.rabbitmq_url)
            self._channel = await self._connection.channel()
            logger.info("✅ MQ Producer connected")
        except Exception as e:
            logger.error(f"❌ MQ Producer connection failed: {e}")
            raise

    async def publish(self, exchange_name: str, routing_key: str, data: dict):
        """Publish a message to a specific exchange."""
        if not self._channel:
            await self.connect()

        exchange = await self._channel.declare_exchange(
            exchange_name, aio_pika.ExchangeType.TOPIC, durable=True,
        )

        message_body = json.dumps(data, ensure_ascii=False)

        await exchange.publish(
            aio_pika.Message(
                body=message_body.encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=routing_key,
        )
        logger.info(f"📤 Published to {exchange_name}/{routing_key}")

    async def publish_trend_result(self, user_id: str, session_id: str,
                                    results: list, location: str):
        """Convenience: publish trend search results."""
        await self.publish(
            MQ_EXCHANGES["SEARCH"],
            MQ_ROUTING_KEYS["TREND_RESULT"],
            {
                "user_id": user_id,
                "session_id": session_id,
                "results": results,
                "location": location,
            },
        )

    async def close(self):
        """Close MQ connection."""
        if self._connection:
            await self._connection.close()
            logger.info("MQ Producer connection closed")
