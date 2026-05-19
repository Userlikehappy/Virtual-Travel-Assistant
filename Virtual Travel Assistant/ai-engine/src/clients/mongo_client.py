"""
LUNA AI Engine - MongoDB Client
Manages chat logs, raw crawler data, and trend results storage.
"""

from pymongo import MongoClient
from pymongo.collection import Collection
from datetime import datetime, timedelta, timezone
from src.config.settings import settings
import logging

logger = logging.getLogger(__name__)


class MongoDBClient:
    """MongoDB client for chat logs, raw data, and trend results."""

    def __init__(self):
        self._client: MongoClient = None
        self._db = None

    def connect(self):
        """Establish connection to MongoDB."""
        try:
            self._client = MongoClient(settings.mongodb_uri)
            self._db = self._client[settings.mongodb_db_name]
            # Test connection
            self._client.admin.command("ping")
            logger.info(f"✅ Connected to MongoDB ({settings.mongodb_db_name})")
            self._ensure_indexes()
        except Exception as e:
            logger.error(f"❌ Failed to connect to MongoDB: {e}")
            raise

    def close(self):
        """Close MongoDB connection."""
        if self._client:
            self._client.close()
            logger.info("MongoDB connection closed")

    def _ensure_indexes(self):
        """Create necessary indexes and TTL indexes."""
        # Chat logs index
        self._db.chat_logs.create_index([("user_id", 1), ("created_at", -1)])
        self._db.chat_logs.create_index([("session_id", 1)])

        # Raw crawler data TTL index (expire after 7 days)
        self._db.raw_crawler_data.create_index(
            "ttl_expires_at", expireAfterSeconds=0
        )

        # Trend results TTL index (expire after 48 hours)
        self._db.trend_results.create_index(
            "expires_at", expireAfterSeconds=0
        )
        self._db.trend_results.create_index([("location", 1), ("created_at", -1)])

    @property
    def chat_logs(self) -> Collection:
        return self._db.chat_logs

    @property
    def raw_crawler_data(self) -> Collection:
        return self._db.raw_crawler_data

    @property
    def trend_results(self) -> Collection:
        return self._db.trend_results

    # === Chat Logs ===

    def save_chat_message(self, user_id: str, session_id: str, role: str,
                          content: str, source: str = None, context: dict = None):
        """Append a message to the chat session log."""
        try:
            message = {
                "role": role,
                "content": content,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            if source:
                message["source"] = source

            self._db.chat_logs.update_one(
                {"user_id": user_id, "session_id": session_id},
                {
                    "$push": {"messages": message},
                    "$set": {"context": context or {}, "updated_at": datetime.now(timezone.utc)},
                    "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
                },
                upsert=True,
            )
        except Exception as e:
            logger.error(f"Failed to save chat message: {e}")

    def get_chat_history(self, session_id: str, limit: int = 20) -> dict:
        """Retrieve chat history for a session."""
        try:
            doc = self._db.chat_logs.find_one(
                {"session_id": session_id},
                {"messages": {"$slice": -limit}},
            )
            return doc if doc else {"messages": []}
        except Exception as e:
            logger.error(f"Failed to get chat history: {e}")
            return {"messages": []}

    # === Trend Results ===

    def save_trend_results(self, query: str, location: str, results: list):
        """Save trend search results with TTL (48 hours)."""
        try:
            self._db.trend_results.insert_one({
                "query": query,
                "location": location,
                "results": results,
                "created_at": datetime.now(timezone.utc),
                "expires_at": datetime.now(timezone.utc) + timedelta(hours=48),
            })
        except Exception as e:
            logger.error(f"Failed to save trend results: {e}")

    def get_recent_trends(self, location: str, hours: int = 3) -> list:
        """Get recent trend results for a location (within TTL)."""
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            doc = self._db.trend_results.find_one(
                {"location": location, "created_at": {"$gte": cutoff}},
                sort=[("created_at", -1)],
            )
            return doc["results"] if doc else []
        except Exception as e:
            logger.error(f"Failed to get recent trends: {e}")
            return []

    # === Raw Crawler Data ===

    def save_raw_data(self, source: str, keyword: str, content: str,
                      media_urls: list = None, engagement: dict = None):
        """Save raw crawler data with 7-day TTL."""
        self._db.raw_crawler_data.insert_one({
            "source": source,
            "keyword": keyword,
            "raw_content": content,
            "media_urls": media_urls or [],
            "engagement": engagement or {},
            "scraped_at": datetime.now(timezone.utc),
            "processed": False,
            "ttl_expires_at": datetime.now(timezone.utc) + timedelta(days=7),
        })

    def get_unprocessed_data(self, limit: int = 100) -> list:
        """Get unprocessed raw data for the pipeline."""
        cursor = self._db.raw_crawler_data.find(
            {"processed": False},
            limit=limit,
        )
        return list(cursor)

    def mark_as_processed(self, doc_id):
        """Mark a raw data document as processed."""
        self._db.raw_crawler_data.update_one(
            {"_id": doc_id},
            {"$set": {"processed": True}},
        )
