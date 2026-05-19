"""
LUNA AI Engine - Milvus Client
Manages connection to Milvus Vector DB for semantic search and persona matching.
"""

from pymilvus import connections, Collection, FieldSchema, CollectionSchema, DataType, utility
from src.config.settings import settings
import logging

logger = logging.getLogger(__name__)


class MilvusClient:
    """Milvus Vector DB client for semantic search operations."""

    LOCATION_COLLECTION = "location_embeddings"
    PERSONA_COLLECTION = "persona_embeddings"
    EMBEDDING_DIM = 768  # paraphrase-multilingual-mpnet-base-v2

    def __init__(self):
        self._connected = False
        self._collections_cache: dict = {}

    def connect(self):
        """Establish connection to Milvus."""
        try:
            connections.connect(
                alias="default",
                uri=settings.milvus_uri,
            )
            self._connected = True
            logger.info(f"✅ Connected to Milvus at {settings.milvus_uri}")
            self._ensure_collections()
        except Exception as e:
            logger.error(f"❌ Failed to connect to Milvus: {e}")
            raise

    def close(self):
        """Close Milvus connection."""
        if self._connected:
            connections.disconnect("default")
            logger.info("Milvus connection closed")

    def _ensure_collections(self):
        """Create collections if they don't exist."""
        if not utility.has_collection(self.LOCATION_COLLECTION):
            self._create_location_collection()
        if not utility.has_collection(self.PERSONA_COLLECTION):
            self._create_persona_collection()

    def _create_location_collection(self):
        """Create location_embeddings collection with HNSW index."""
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=self.EMBEDDING_DIM),
            FieldSchema(name="name", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="city", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="district", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="environment", dtype=DataType.VARCHAR, max_length=32),
            FieldSchema(name="trust_rank", dtype=DataType.FLOAT),
            FieldSchema(name="season_tags", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="style", dtype=DataType.VARCHAR, max_length=32),
        ]
        schema = CollectionSchema(fields=fields, description="Location embeddings for semantic search")
        collection = Collection(name=self.LOCATION_COLLECTION, schema=schema)

        # Create HNSW index for high recall
        index_params = {
            "metric_type": "COSINE",
            "index_type": "HNSW",
            "params": {"M": 16, "efConstruction": 256},
        }
        collection.create_index("embedding", index_params)
        logger.info(f"✅ Created collection: {self.LOCATION_COLLECTION}")

    def _create_persona_collection(self):
        """Create persona_embeddings collection."""
        fields = [
            FieldSchema(name="user_id", dtype=DataType.INT64, is_primary=True),
            FieldSchema(name="persona_vector", dtype=DataType.FLOAT_VECTOR, dim=self.EMBEDDING_DIM),
            FieldSchema(name="age_group", dtype=DataType.VARCHAR, max_length=32),
            FieldSchema(name="budget_level", dtype=DataType.VARCHAR, max_length=16),
            FieldSchema(name="updated_at", dtype=DataType.INT64),
        ]
        schema = CollectionSchema(fields=fields, description="User persona vectors")
        collection = Collection(name=self.PERSONA_COLLECTION, schema=schema)

        index_params = {
            "metric_type": "COSINE",
            "index_type": "HNSW",
            "params": {"M": 16, "efConstruction": 256},
        }
        collection.create_index("persona_vector", index_params)
        logger.info(f"✅ Created collection: {self.PERSONA_COLLECTION}")

    def _get_collection(self, name: str) -> Collection:
        """Get a collection by name, loading it once and caching."""
        if name not in self._collections_cache:
            collection = Collection(name)
            collection.load()
            self._collections_cache[name] = collection
        return self._collections_cache[name]

    def search_locations(
        self,
        query_embedding: list[float],
        environment_filter: str = None,
        city_filter: str = None,
        min_trust: float = 0.0,
        limit: int = 5,
    ) -> list[dict]:
        """Search for semantically similar locations."""
        try:
            collection = self._get_collection(self.LOCATION_COLLECTION)

            # Build filter expression
            expr_parts = []
            if environment_filter:
                expr_parts.append(f'environment == "{environment_filter}"')
            if city_filter:
                # Basic string match
                expr_parts.append(f'city like "%{city_filter}%"')
            if min_trust > 0:
                expr_parts.append(f"trust_rank > {min_trust}")
            expr = " AND ".join(expr_parts) if expr_parts else None

            results = collection.search(
                data=[query_embedding],
                anns_field="embedding",
                param={"metric_type": "COSINE", "params": {"ef": 128}},
                limit=limit,
                expr=expr,
                output_fields=["name", "district", "category", "environment", "trust_rank", "style"],
            )

            locations = []
            for hit in results[0]:
                locations.append({
                    "id": hit.id,
                    "name": hit.entity.get("name"),
                    "district": hit.entity.get("district"),
                    "category": hit.entity.get("category"),
                    "environment": hit.entity.get("environment"),
                    "trust_rank": hit.entity.get("trust_rank"),
                    "style": hit.entity.get("style"),
                    "similarity": hit.score,
                })
            return locations
        except Exception as e:
            logger.error(f"Milvus search_locations failed: {e}")
            return []

    def search_persona_match(self, user_vector: list[float], limit: int = 10) -> list[dict]:
        """Find locations most similar to user's persona vector."""
        try:
            collection = self._get_collection(self.LOCATION_COLLECTION)

            results = collection.search(
                data=[user_vector],
                anns_field="embedding",
                param={"metric_type": "COSINE", "params": {"ef": 128}},
                limit=limit,
                output_fields=["name", "district", "category", "trust_rank", "style"],
            )

            return [
                {
                    "name": hit.entity.get("name"),
                    "category": hit.entity.get("category"),
                    "similarity": hit.score,
                }
                for hit in results[0]
            ]
        except Exception as e:
            logger.error(f"Milvus search_persona_match failed: {e}")
            return []

    def update_trust_rank(self, location_id: int, new_trust: float):
        """Update trust_rank for a location (Feedback Loop)."""
        # Milvus doesn't support in-place update easily;
        # in production, use upsert or delete+insert pattern
        logger.info(f"Trust rank update requested for location {location_id} -> {new_trust}")
