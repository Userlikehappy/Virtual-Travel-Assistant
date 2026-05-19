"""
LUNA Data Pipeline — Main Runner
Orchestrates the full data pipeline: Crawl → Process → Embed → Load.
"""

import sys
import logging
from pathlib import Path

# Add parent to PATH
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-30s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("luna.pipeline")


def main():
    """Run the full data pipeline."""
    logger.info("=" * 60)
    logger.info("🔄 LUNA Data Pipeline Starting...")
    logger.info("=" * 60)

    # === Step 1: Crawl Static Data ===
    logger.info("\n📥 STEP 1: Crawling static data...")
    from crawlers.static_crawler import crawl_static_data, save_raw_data
    raw_data = crawl_static_data()
    save_raw_data(raw_data, "data/raw")
    logger.info(f"✅ Step 1 complete: {len(raw_data)} records crawled")

    # === Step 2: Load to Neo4j Knowledge Graph ===
    logger.info("\n🔗 STEP 2: Loading to Neo4j Knowledge Graph...")
    try:
        import os
        from loaders.neo4j_loader import load_to_neo4j
        load_to_neo4j(
            raw_data,
            uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            user=os.getenv("NEO4J_USER", "neo4j"),
            password=os.getenv("NEO4J_PASSWORD", "password"),
        )
        logger.info("✅ Step 2 complete: Knowledge Graph populated")
    except Exception as e:
        logger.warning(f"⚠️ Step 2 skipped (Neo4j not available): {e}")

    # === Step 3: Embed & Load to Milvus ===
    logger.info("\n🧠 STEP 3: Embedding & loading to Milvus...")
    try:
        from processors.embedder import embed_and_load
        embed_and_load(
            raw_data,
            milvus_uri=os.getenv("MILVUS_URI", "http://localhost:19530"),
        )
        logger.info("✅ Step 3 complete: Vectors loaded to Milvus")
    except Exception as e:
        logger.warning(f"⚠️ Step 3 skipped (Milvus not available): {e}")

    # === Done ===
    logger.info("=" * 60)
    logger.info("🎉 LUNA Data Pipeline COMPLETE!")
    logger.info(f"   Total records processed: {len(raw_data)}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
