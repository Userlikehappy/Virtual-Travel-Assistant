"""
LUNA AI Engine — Main Entry Point
Assembles all services and starts gRPC server + MQ consumers.
This is the ONLY file that wires everything together (Root).
"""

import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-30s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("luna.main")


def _extract_city(district: str) -> str:
    d = district or ""
    if "Đà Nẵng" in d or "Da Nang" in d: return "Đà Nẵng"
    if "Hội An" in d or "Hoi An" in d: return "Hội An"
    if "Huế" in d or "Hue" in d or "Thừa Thiên" in d: return "Huế"
    return d


def _auto_seed_if_empty(neo4j_client, milvus_client):
    """
    On first startup (empty databases), automatically load seed data
    from data/seed/. Safe to call every startup — exits immediately if
    databases already have data.
    """
    seed_dir = Path(__file__).parent / "data" / "seed"
    if not seed_dir.exists():
        return

    # Check Neo4j
    try:
        rows = neo4j_client.query("MATCH (n) RETURN count(n) AS c")
        neo4j_count = rows[0]["c"] if rows else 0
    except Exception:
        neo4j_count = 0

    # Check Milvus
    try:
        from pymilvus import Collection, utility
        milvus_count = 0
        if utility.has_collection("location_embeddings"):
            col = Collection("location_embeddings")
            col.load()
            milvus_count = col.num_entities
    except Exception:
        milvus_count = 0

    # Count expected records so that adding more seed data triggers a re-seed automatically.
    expected = 0
    for fname in ["locations.json", "food.json"]:
        fpath = seed_dir / fname
        if fpath.exists():
            with open(fpath, "r", encoding="utf-8") as f:
                expected += len(json.load(f))

    if neo4j_count >= expected and milvus_count >= expected:
        logger.info(f"✅ Databases already seeded (Neo4j: {neo4j_count} nodes, Milvus: {milvus_count} vectors) — skipping auto-seed")
        return

    if neo4j_count > 0 or milvus_count > 0:
        logger.info(f"🔄 Seed data grew ({expected} records expected, DB has {neo4j_count}/{milvus_count}) — re-seeding...")
    else:
        logger.info("🌱 Databases empty — running auto-seed from data/seed/...")

    all_data = []
    for fname in ["locations.json", "food.json"]:
        fpath = seed_dir / fname
        if fpath.exists():
            with open(fpath, "r", encoding="utf-8") as f:
                records = json.load(f)
                all_data.extend(records)
                logger.info(f"   Loaded {len(records)} records from {fname}")

    if not all_data:
        logger.warning("   No seed files found — skipping auto-seed")
        return

    # ── Seed Neo4j ────────────────────────────────────────────────
    try:
        neo4j_client.query("MATCH (n) DETACH DELETE n")
        for entity in all_data:
            name = entity.get("name", "").strip()
            if not name:
                continue
            city = entity.get("city", "") or _extract_city(entity.get("district", ""))
            ticket = entity.get("ticket_price", {})
            est = ticket.get("adult", 0) if isinstance(ticket, dict) else 0
            ec = entity.get("estimated_cost", {})
            if isinstance(ec, dict): est = ec.get("max", est)
            label = "FoodPlace" if entity.get("type") == "food" else "Location"
            props = {
                "city": city, "district": entity.get("district", ""),
                "category": entity.get("category", ""), "environment": entity.get("environment", "Outdoor"),
                "lat": float(entity.get("lat", 0.0) or 0.0), "lng": float(entity.get("lng", 0.0) or 0.0),
                "description": entity.get("description", ""), "operating_hours": entity.get("operating_hours", ""),
                "style": entity.get("style", "heritage"), "estimated_cost": float(est),
                "meal_time": entity.get("meal_time", []),
                "car_parking": bool(entity.get("car_parking", False)),
                "cuisine_type": entity.get("cuisine_type", ""),
            }
            neo4j_client.query(f"MERGE (n:{label} {{name: $name}}) SET n += $props", {"name": name, "props": props})

        neo4j_client.query("""
            MATCH (a), (b)
            WHERE (a:Location OR a:FoodPlace) AND (b:Location OR b:FoodPlace)
              AND a.city = b.city AND a.city <> '' AND a <> b
            MERGE (a)-[:NEAR]->(b)
        """)
        for entity in all_data:
            name = entity.get("name", "").strip()
            if not name: continue
            wtype = "rainy" if entity.get("environment") == "Indoor" else "sunny"
            neo4j_client.query(
                "MERGE (w:WeatherCondition {type:$t}) WITH w MATCH (n {name:$n}) MERGE (n)-[:SUITABLE_FOR]->(w)",
                {"t": wtype, "n": name}
            )
        count = neo4j_client.query("MATCH (n) RETURN count(n) AS c")[0]["c"]
        logger.info(f"   ✅ Neo4j seeded: {count} nodes")
    except Exception as e:
        logger.error(f"   ❌ Neo4j seed failed: {e}")

    # ── Seed Milvus ───────────────────────────────────────────────
    try:
        from pymilvus import Collection, FieldSchema, CollectionSchema, DataType, utility
        from sentence_transformers import SentenceTransformer
        from src.utils.embedding import embed_text

        col_name = "location_embeddings"
        if utility.has_collection(col_name):
            utility.drop_collection(col_name)

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
        schema = CollectionSchema(fields=fields, description="Location embeddings")
        col = Collection(name=col_name, schema=schema)
        col.create_index("embedding", {"metric_type": "COSINE", "index_type": "HNSW", "params": {"M": 16, "efConstruction": 256}})

        model = SentenceTransformer("paraphrase-multilingual-mpnet-base-v2")
        valid = [e for e in all_data if e.get("name", "").strip()]
        texts = [f"{e.get('name','')}. {e.get('description','')}. {e.get('category','')}. {_extract_city(e.get('district',''))}" for e in valid]
        embeddings = model.encode(texts, normalize_embeddings=True, batch_size=32)

        col.insert([
            embeddings.tolist(),
            [e.get("name", "") for e in valid],
            [_extract_city(e.get("district", "")) for e in valid],
            [e.get("district", "") for e in valid],
            [e.get("category", "") for e in valid],
            [e.get("environment", "Outdoor") for e in valid],
            [0.85] * len(valid),
            [",".join(e.get("season_tags", ["all_year"])) for e in valid],
            [e.get("style", "heritage") for e in valid],
        ])
        col.flush()
        col.load()
        # Invalidate milvus client collection cache so it reloads
        milvus_client._collections_cache.clear()
        logger.info(f"   ✅ Milvus seeded: {col.num_entities} vectors")
    except Exception as e:
        logger.error(f"   ❌ Milvus seed failed: {e}")

    logger.info("🌱 Auto-seed complete")

from src.config.settings import settings

# === Import Clients ===
from src.clients.neo4j_client import Neo4jClient
from src.clients.milvus_client import MilvusClient
from src.clients.mongo_client import MongoDBClient
from src.clients.redis_client import RedisClient

# === Import Services ===
from src.services.chat_logic import ChatLogic
from src.services.itinerary_logic import ItineraryLogic
from src.services.scoring_engine import ScoringEngine
from src.services.trend_agent import TrendAgent
from src.services.agent_team import AgentTeam

# === Import Infrastructure ===
from src.grpc_server.server import LunaAIServicer, create_grpc_server
from src.message_queue.consumer import MQConsumer


def main():
    """Main entry point — assemble and run all components."""

    logger.info("=" * 60)
    logger.info("🌙 LUNA AI Engine Starting...")
    logger.info("=" * 60)

    # === Step 1: Initialize Clients ===
    logger.info("📦 Initializing database clients...")

    neo4j_client = Neo4jClient()
    milvus_client = MilvusClient()
    mongo_client = MongoDBClient()
    redis_client = RedisClient()

    try:
        neo4j_client.connect()
    except Exception as e:
        logger.warning(f"⚠️ Neo4j connection failed (will retry): {e}")

    try:
        milvus_client.connect()
    except Exception as e:
        logger.warning(f"⚠️ Milvus connection failed (will retry): {e}")

    try:
        mongo_client.connect()
    except Exception as e:
        logger.warning(f"⚠️ MongoDB connection failed (will retry): {e}")

    try:
        redis_client.connect()
    except Exception as e:
        logger.warning(f"⚠️ Redis connection failed (will retry): {e}")

    # === Step 1b: Auto-seed databases if empty (first boot) ===
    try:
        _auto_seed_if_empty(neo4j_client, milvus_client)
    except Exception as e:
        logger.warning(f"⚠️ Auto-seed skipped: {e}")

    # === Step 2: Initialize Services (Fat Layer) ===
    logger.info("🧠 Initializing AI services...")

    scoring_engine = ScoringEngine()

    trend_agent = TrendAgent(
        redis_client=redis_client,
        mongo_client=mongo_client,
    )

    chat_logic = ChatLogic(
        neo4j_client=neo4j_client,
        milvus_client=milvus_client,
        mongo_client=mongo_client,
        trend_agent=trend_agent,
    )

    agent_team = AgentTeam()

    itinerary_logic = ItineraryLogic(
        neo4j_client=neo4j_client,
        milvus_client=milvus_client,
        scoring_engine=scoring_engine,
        trend_agent=trend_agent,
        redis_client=redis_client,
        agent_team=agent_team,
    )

    # === Step 3: Create gRPC Server (Thin Layer) ===
    logger.info("🔌 Setting up gRPC server...")

    servicer = LunaAIServicer(
        chat_logic=chat_logic,
        itinerary_logic=itinerary_logic,
        trend_agent=trend_agent,
    )

    grpc_server = create_grpc_server(
        servicer=servicer,
        host=settings.grpc_host,
        port=settings.grpc_port,
        max_workers=settings.grpc_max_workers,
    )

    # === Step 4: Start gRPC Server ===
    grpc_server.start()
    logger.info(f"✅ gRPC Server listening on {settings.grpc_host}:{settings.grpc_port}")
    logger.info(f"   Workers: {settings.grpc_max_workers}")

    # === Step 5: Start MQ Consumer (async) ===
    async def start_mq_and_wait():
        try:
            mq_consumer = MQConsumer(trend_agent=trend_agent)
            await mq_consumer.start_consuming()
            logger.info("✅ MQ Consumer started")
        except Exception as e:
            logger.warning(f"⚠️ MQ Consumer failed to start: {e}")
            logger.info("   gRPC server will continue without MQ support")
        
        # Keep running the event loop to keep gRPC server alive and MQ active
        while True:
            await asyncio.sleep(3600)


    # === Step 6: Graceful Shutdown ===
    def shutdown(signum, frame):
        logger.info("🔴 Shutting down LUNA AI Engine...")
        grpc_server.stop(grace=5)
        neo4j_client.close()
        milvus_client.close()
        mongo_client.close()
        redis_client.close()
        logger.info("👋 Goodbye!")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # === Run MQ in background, gRPC in foreground ===
    logger.info("=" * 60)
    logger.info("🌙 LUNA AI Engine is READY")
    logger.info(f"   gRPC: {settings.grpc_host}:{settings.grpc_port}")
    logger.info(f"   Neo4j: {settings.neo4j_uri}")
    logger.info(f"   Milvus: {settings.milvus_uri}")
    logger.info(f"   MongoDB: {settings.mongodb_db_name}")
    logger.info(f"   Redis: {settings.redis_url}")
    logger.info(f"   RabbitMQ: {settings.rabbitmq_url}")
    logger.info("=" * 60)

    try:
        # Start MQ consumer in async loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start_mq_and_wait())
    except KeyboardInterrupt:
        shutdown(None, None)


if __name__ == "__main__":
    main()
