"""
Standalone seed script — loads data/raw/ into Neo4j and Milvus.
Run inside the AI engine container:
  docker exec luna_ai_engine python /tmp/seed_databases.py
"""
import json
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
logger = logging.getLogger("seed")

RAW_DIR = os.path.join(os.path.dirname(__file__), "data", "raw")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASSWORD", "password")
MILVUS_URI = os.getenv("MILVUS_URI", "http://milvus:19530")
MODEL_NAME = os.getenv("EMBEDDING_MODEL", "paraphrase-multilingual-mpnet-base-v2")


def load_raw_data():
    all_data = []
    for fname in ["locations.json", "food.json"]:
        fpath = os.path.join(RAW_DIR, fname)
        if os.path.exists(fpath):
            with open(fpath, "r", encoding="utf-8") as f:
                records = json.load(f)
                all_data.extend(records)
                logger.info(f"  Loaded {len(records)} records from {fname}")
        else:
            logger.warning(f"  Not found: {fpath}")
    return all_data


def extract_city(district: str) -> str:
    d = district or ""
    if "Đà Nẵng" in d or "Da Nang" in d:
        return "Đà Nẵng"
    if "Hội An" in d or "Hoi An" in d:
        return "Hội An"
    if "Huế" in d or "Hue" in d or "Thừa Thiên" in d:
        return "Huế"
    return d


def seed_neo4j(data: list):
    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    driver.verify_connectivity()
    logger.info(f"Connected to Neo4j at {NEO4J_URI}")

    with driver.session() as s:
        # Clear
        s.run("MATCH (n) DETACH DELETE n")
        logger.info("  Cleared existing data")

        # Indexes
        for q in [
            "CREATE INDEX IF NOT EXISTS FOR (l:Location) ON (l.name)",
            "CREATE INDEX IF NOT EXISTS FOR (f:FoodPlace) ON (f.name)",
            "CREATE INDEX IF NOT EXISTS FOR (l:Location) ON (l.city)",
            "CREATE INDEX IF NOT EXISTS FOR (f:FoodPlace) ON (f.city)",
        ]:
            s.run(q)

        # Fulltext index
        try:
            s.run("""
                CREATE FULLTEXT INDEX locationIndex IF NOT EXISTS
                FOR (n:Location|FoodPlace) ON EACH [n.name, n.description, n.category, n.city, n.district]
            """)
        except Exception as e:
            logger.warning(f"  Fulltext index: {e}")

        loaded = 0
        for entity in data:
            name = entity.get("name", "").strip()
            if not name:
                continue

            city = extract_city(entity.get("district", ""))
            ticket = entity.get("ticket_price", {})
            est_cost = ticket.get("adult", 0) if isinstance(ticket, dict) else 0
            ec = entity.get("estimated_cost", {})
            if isinstance(ec, dict):
                est_cost = ec.get("max", est_cost)

            props = {
                "city": city,
                "district": entity.get("district", ""),
                "category": entity.get("category", "food" if entity.get("type") == "food" else ""),
                "environment": entity.get("environment", "Outdoor"),
                "lat": float(entity.get("lat", 0.0) or 0.0),
                "lng": float(entity.get("lng", 0.0) or 0.0),
                "description": entity.get("description", ""),
                "operating_hours": entity.get("operating_hours", ""),
                "style": entity.get("style", "heritage"),
                "estimated_cost": float(est_cost),
                "meal_time": entity.get("meal_time", []),
                "car_parking": bool(entity.get("car_parking", False)),
                "motorbike_parking": bool(entity.get("motorbike_parking", True)),
                "visit_duration_minutes": int(entity.get("visit_duration_minutes", 60)),
                "is_spiritual": bool(entity.get("is_spiritual", False)),
            }
            # Food-specific
            if entity.get("type") == "food":
                props["cuisine_type"] = entity.get("cuisine_type", "")
                props["price_range"] = entity.get("price_range", "")
                props["alley_access"] = bool(entity.get("alley_access", False))
                label = "FoodPlace"
            else:
                props["historical_significance"] = entity.get("historical_significance", "")
                label = "Location"

            s.run(
                f"MERGE (n:{label} {{name: $name}}) SET n += $props",
                {"name": name, "props": props},
            )
            loaded += 1

        # NEAR relationships (same city)
        s.run("""
            MATCH (a), (b)
            WHERE (a:Location OR a:FoodPlace) AND (b:Location OR b:FoodPlace)
              AND a.city = b.city AND a.city <> '' AND a <> b
            MERGE (a)-[:NEAR]->(b)
        """)
        logger.info("  Created NEAR relationships")

        # Weather suitability
        for entity in data:
            name = entity.get("name", "").strip()
            if not name:
                continue
            wtype = "rainy" if entity.get("environment") == "Indoor" else "sunny"
            s.run("""
                MERGE (w:WeatherCondition {type: $wtype})
                WITH w
                MATCH (n {name: $name})
                MERGE (n)-[:SUITABLE_FOR]->(w)
            """, {"wtype": wtype, "name": name})

        count = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        logger.info(f"  ✅ Neo4j seeded: {loaded} entities, {count} total nodes")

    driver.close()


def seed_milvus(data: list):
    from pymilvus import connections, Collection, FieldSchema, CollectionSchema, DataType, utility
    from sentence_transformers import SentenceTransformer

    connections.connect(alias="default", uri=MILVUS_URI)
    logger.info(f"Connected to Milvus at {MILVUS_URI}")

    collection_name = "location_embeddings"

    # Drop and recreate to match AI engine schema
    if utility.has_collection(collection_name):
        utility.drop_collection(collection_name)
        logger.info(f"  Dropped existing collection '{collection_name}'")

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
    collection.create_index("embedding", {
        "metric_type": "COSINE",
        "index_type": "HNSW",
        "params": {"M": 16, "efConstruction": 256},
    })
    logger.info(f"  Created collection '{collection_name}'")

    # Filter valid entities
    valid = [e for e in data if e.get("name", "").strip()]

    logger.info(f"  Loading embedding model '{MODEL_NAME}'...")
    model = SentenceTransformer(MODEL_NAME)

    def make_text(e):
        parts = [e.get("name", ""), e.get("description", ""), f"Loại: {e.get('category', '')}",
                 f"Khu vực: {e.get('district', '')}", f"Thành phố: {extract_city(e.get('district', ''))}",
                 e.get("cuisine_type", ""), e.get("historical_significance", ""),
                 f"Môi trường: {e.get('environment', '')}", f"Phong cách: {e.get('style', '')}"]
        return ". ".join(p for p in parts if p)

    texts = [make_text(e) for e in valid]
    logger.info(f"  Embedding {len(texts)} entities...")
    embeddings = model.encode(texts, normalize_embeddings=True, batch_size=32)

    insert_rows = [
        embeddings.tolist(),
        [e.get("name", "") for e in valid],
        [extract_city(e.get("district", "")) for e in valid],
        [e.get("district", "") for e in valid],
        [e.get("category", "") for e in valid],
        [e.get("environment", "Outdoor") for e in valid],
        [0.85] * len(valid),
        [",".join(e.get("season_tags", ["all_year"])) for e in valid],
        [e.get("style", "heritage") for e in valid],
    ]

    collection.insert(insert_rows)
    collection.flush()
    collection.load()
    logger.info(f"  ✅ Milvus seeded: {collection.num_entities} entities in '{collection_name}'")

    connections.disconnect("default")


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("🌱 LUNA Database Seed")
    logger.info("=" * 60)

    data = load_raw_data()
    if not data:
        logger.error("No data found — check data/raw/locations.json and food.json")
        sys.exit(1)

    logger.info(f"\nLoaded {len(data)} total records")

    logger.info("\n🔗 Seeding Neo4j...")
    try:
        seed_neo4j(data)
    except Exception as e:
        logger.error(f"Neo4j seed failed: {e}")
        import traceback; traceback.print_exc()

    logger.info("\n🧠 Seeding Milvus...")
    try:
        seed_milvus(data)
    except Exception as e:
        logger.error(f"Milvus seed failed: {e}")
        import traceback; traceback.print_exc()

    logger.info("\n✅ Seed complete!")
