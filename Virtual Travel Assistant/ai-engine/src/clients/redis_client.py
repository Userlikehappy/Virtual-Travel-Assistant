"""
LUNA AI Engine - Redis Client
Manages trend caching (TTL 3h) and pub/sub for real-time events.
"""

import json
import redis
from src.config.settings import settings
import logging

logger = logging.getLogger(__name__)


class RedisClient:
    """Redis client for trend caching and pub/sub."""

    TREND_TTL_SECONDS = 3 * 60 * 60  # 3 hours
    TREND_KEY_PREFIX = "trend:"

    def __init__(self):
        self._client: redis.Redis = None

    def connect(self):
        """Establish connection to Redis."""
        try:
            self._client = redis.from_url(
                settings.redis_url,
                decode_responses=True,
            )
            self._client.ping()
            logger.info(f"✅ Connected to Redis at {settings.redis_url}")
        except Exception as e:
            logger.error(f"❌ Failed to connect to Redis: {e}")
            raise

    def close(self):
        """Close Redis connection."""
        if self._client:
            self._client.close()
            logger.info("Redis connection closed")

    # === Trend Caching ===

    def _trend_key(self, location: str, query_hash: str) -> str:
        """Generate cache key for trend queries."""
        location_clean = location.lower().replace(" ", "_")
        return f"{self.TREND_KEY_PREFIX}{location_clean}:{query_hash}"

    def get_cached_trend(self, location: str, query_hash: str) -> list | None:
        """Get cached trend results. Returns None on cache miss."""
        key = self._trend_key(location, query_hash)
        data = self._client.get(key)
        if data:
            logger.info(f"🎯 Cache HIT for trend: {key}")
            return json.loads(data)
        logger.info(f"❌ Cache MISS for trend: {key}")
        return None

    def cache_trend(self, location: str, query_hash: str, results: list):
        """Cache trend results with 3-hour TTL."""
        key = self._trend_key(location, query_hash)
        self._client.setex(
            key,
            self.TREND_TTL_SECONDS,
            json.dumps(results, ensure_ascii=False),
        )
        logger.info(f"💾 Cached trend: {key} (TTL={self.TREND_TTL_SECONDS}s)")

    # === Pub/Sub ===

    def publish(self, channel: str, message: dict):
        """Publish a message to a Redis channel."""
        self._client.publish(channel, json.dumps(message, ensure_ascii=False))

    def subscribe(self, channel: str):
        """Subscribe to a Redis channel. Returns pubsub object."""
        pubsub = self._client.pubsub()
        pubsub.subscribe(channel)
        return pubsub

    # === Generic Cache ===

    def set_cache(self, key: str, value: any, ttl_seconds: int = 300):
        """Set a generic cache entry with TTL."""
        try:
            self._client.setex(key, ttl_seconds, json.dumps(value, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Redis set_cache failed for {key}: {e}")

    def get_cache(self, key: str) -> any:
        """Get a generic cache entry."""
        try:
            data = self._client.get(key)
            return json.loads(data) if data else None
        except Exception as e:
            logger.error(f"Redis get_cache failed for {key}: {e}")
            return None

    def delete_cache(self, key: str):
        """Delete a cache entry."""
        try:
            self._client.delete(key)
        except Exception as e:
            logger.error(f"Redis delete_cache failed for {key}: {e}")
