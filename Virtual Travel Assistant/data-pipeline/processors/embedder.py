"""
LUNA Data Pipeline - Embedding & Vector Ingestion
Processes cleaned data into embeddings and loads into Milvus.
"""

import json
import logging
import os
from pathlib import Path
from sentence_transformers import SentenceTransformer
from pymilvus import connections, Collection, utility

logger = logging.getLogger(__name__)


def _extract_city(district: str) -> str:
    d = district or ""
    if "Đà Nẵng" in d or "Da Nang" in d:
        return "Đà Nẵng"
    if "Hội An" in d or "Hoi An" in d:
        return "Hội An"
    if "Huế" in d or "Hue" in d or "Thừa Thiên" in d:
        return "Huế"
    return d


def create_embedding_text(entity: dict) -> str:
    """Create a rich text representation of an entity for embedding."""
    parts = []

    parts.append(entity.get("name", ""))

    if entity.get("description"):
        parts.append(entity["description"])

    if entity.get("category"):
        parts.append(f"Loại: {entity['category']}")

    if entity.get("district"):
        parts.append(f"Khu vực: {entity['district']}")

    if entity.get("cuisine_type"):
        parts.append(f"Ẩm thực: {entity['cuisine_type']}")

    if entity.get("historical_significance"):
        parts.append(entity["historical_significance"])

    if entity.get("environment"):
        parts.append(f"Môi trường: {entity['environment']}")

    if entity.get("style"):
        parts.append(f"Phong cách: {entity['style']}")

    return ". ".join(parts)


def embed_and_load(data: list, milvus_uri: str = None,
                   model_name: str = "paraphrase-multilingual-mpnet-base-v2"):
    """
    Embed all entities and load into Milvus.
    """
    milvus_uri = milvus_uri or os.getenv("MILVUS_URI", "http://localhost:19530")
    logger.info(f"📥 Loading embedding model: {model_name}")
    model = SentenceTransformer(model_name)

    # Connect to Milvus
    connections.connect(alias="default", uri=milvus_uri)
    collection_name = "location_embeddings"

    if not utility.has_collection(collection_name):
        logger.warning(f"Collection {collection_name} doesn't exist. Run AI Engine first to create it.")
        return

    # DEDUPLICATION FLAG
    # LUNA's Vector DB is treated as an ephemeral index. 
    # Therefore, dropping and recreating is the safest way to avoid duplicates.
    try:
        logger.info(f"🧹 Dropping old collection '{collection_name}' to prevent duplicate vectors...")
        utility.drop_collection(collection_name)
        logger.info("✅ Old data cleared. The AI Engine will auto-recreate the schema upon next restart.")
        # We must exit here and let the user restart AI Engine to recreate schema, 
        # or we could recreate it here (but better to keep it DRY).
        # Actually, let's just recreate it here to keep the pipeline standalone.
        from pymilvus import FieldSchema, CollectionSchema, DataType
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=768),
            FieldSchema(name="name", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="city", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="district", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="environment", dtype=DataType.VARCHAR, max_length=32),
            FieldSchema(name="trust_rank", dtype=DataType.FLOAT),
            FieldSchema(name="season_tags", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="style", dtype=DataType.VARCHAR, max_length=32),
        ]
        schema = CollectionSchema(fields=fields, description="Location embeddings for semantic search")
        collection = Collection(name=collection_name, schema=schema)
        
        index_params = {
            "metric_type": "COSINE",
            "index_type": "HNSW",
            "params": {"M": 16, "efConstruction": 256},
        }
        collection.create_index("embedding", index_params)
        logger.info("✅ Schema recreated successfully.")
    except Exception as e:
        logger.warning(f"⚠️ Schema recreation failed: {e}")
        collection = Collection(collection_name)

    # Prepare data
    texts = [create_embedding_text(d) for d in data]
    logger.info(f"🔄 Embedding {len(texts)} entities...")
    embeddings = model.encode(texts, normalize_embeddings=True, batch_size=32)

    # Prepare insertion data
    insert_data = {
        "embedding": embeddings.tolist(),
        "name": [d.get("name", "") for d in data],
        "city": [_extract_city(d.get("district", "")) for d in data],
        "district": [d.get("district", "") for d in data],
        "category": [d.get("category", "") for d in data],
        "environment": [d.get("environment", "Outdoor") for d in data],
        "trust_rank": [0.85 for _ in data],  # Default trust
        "season_tags": [",".join(d.get("season_tags", ["all_year"])) for d in data],
        "style": [d.get("style", "heritage") for d in data],
    }

    # Insert into Milvus (order must match schema field order after id/embedding)
    collection.insert([
        insert_data["embedding"],
        insert_data["name"],
        insert_data["city"],
        insert_data["district"],
        insert_data["category"],
        insert_data["environment"],
        insert_data["trust_rank"],
        insert_data["season_tags"],
        insert_data["style"],
    ])

    collection.flush()
    logger.info(f"✅ Loaded {len(data)} embeddings into Milvus ({collection_name})")

    connections.disconnect("default")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    raw_dir = Path("data/raw")
    all_data = []

    for f in ["locations.json", "food.json"]:
        fpath = raw_dir / f
        if fpath.exists():
            with open(fpath, "r", encoding="utf-8") as file:
                all_data.extend(json.load(file))

    if all_data:
        embed_and_load(all_data)
    else:
        logger.warning("No data found. Run static_crawler.py first.")
