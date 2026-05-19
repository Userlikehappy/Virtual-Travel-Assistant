"""
LUNA Data Pipeline - Neo4j Knowledge Graph Loader
Creates entities and relationships in the Knowledge Graph.
"""

import json
import logging
from pathlib import Path
from neo4j import GraphDatabase

logger = logging.getLogger(__name__)

import os


def _extract_city(district: str) -> str:
    """Derive city name from district string."""
    d = district or ""
    if "Đà Nẵng" in d or "Da Nang" in d:
        return "Đà Nẵng"
    if "Hội An" in d or "Hoi An" in d:
        return "Hội An"
    if "Huế" in d or "Hue" in d or "Thừa Thiên" in d:
        return "Huế"
    return d  # fallback: keep as-is


def load_to_neo4j(data: list, uri: str = None,
                   user: str = None, password: str = None):
    """
    Load entities and relationships into Neo4j Knowledge Graph.

    Entity Types: Location, FoodPlace, CulturalFact, WeatherCondition, Tag, Dynasty
    Relationships: NEAR, HAS_TAG, SUITABLE_FOR, ABOUT, HAPPENED_IN, SERVES
    """
    uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = user or os.getenv("NEO4J_USER", "neo4j")
    password = password or os.getenv("NEO4J_PASSWORD", "password")
    driver = GraphDatabase.driver(uri, auth=(user, password))
    logger.info(f"✅ Connected to Neo4j at {uri}")

    with driver.session() as session:
        # DEDUPLICATION: Clear all existing data to prevent graph bloat
        try:
            logger.info("🧹 Clearing old data in Neo4j...")
            session.run("MATCH (n) DETACH DELETE n")
            logger.info("✅ Neo4j database cleared.")
        except Exception as e:
            logger.warning(f"⚠️ Could not clear Neo4j: {e}")

        # Create indexes
        session.run("CREATE INDEX IF NOT EXISTS FOR (l:Location) ON (l.name)")
        session.run("CREATE INDEX IF NOT EXISTS FOR (f:FoodPlace) ON (f.name)")
        session.run("CREATE INDEX IF NOT EXISTS FOR (t:Tag) ON (t.name)")

        # Create fulltext index for RAG search
        try:
            session.run("""
                CREATE FULLTEXT INDEX locationIndex IF NOT EXISTS
                FOR (l:Location|FoodPlace) ON EACH [l.name, l.description, l.category]
            """)
        except Exception:
            pass  # Index may already exist

        # Load locations & food
        for entity in data:
            if entity.get("type") == "food":
                _create_food_node(session, entity)
            else:
                _create_location_node(session, entity)

        # Create NEAR relationships between nearby locations
        _create_proximity_relationships(session)

        # Create weather suitability relationships
        _create_weather_relationships(session, data)

    driver.close()
    logger.info(f"✅ Loaded {len(data)} entities into Neo4j Knowledge Graph")


def _create_location_node(session, entity: dict):
    """Create a Location node with all metadata."""
    # Derive city from district
    city = _extract_city(entity.get("district", ""))
    # Derive estimated_cost from ticket_price or fallback
    ticket = entity.get("ticket_price", {})
    if isinstance(ticket, dict):
        est_cost = ticket.get("adult", 0)
    else:
        est_cost = 0
    ec = entity.get("estimated_cost", {})
    if isinstance(ec, dict):
        est_cost = ec.get("max", est_cost)

    session.run("""
        MERGE (loc:Location {name: $name})
        SET loc.category = $category,
            loc.city = $city,
            loc.district = $district,
            loc.environment = $environment,
            loc.lat = $lat,
            loc.lng = $lng,
            loc.description = $description,
            loc.operating_hours = $operating_hours,
            loc.style = $style,
            loc.car_parking = $car_parking,
            loc.motorbike_parking = $motorbike_parking,
            loc.visit_duration_minutes = $visit_duration,
            loc.is_spiritual = $is_spiritual,
            loc.estimated_cost = $estimated_cost,
            loc.meal_time = $meal_time
    """, {
        "name": entity.get("name", ""),
        "category": entity.get("category", ""),
        "city": city,
        "district": entity.get("district", ""),
        "environment": entity.get("environment", "Outdoor"),
        "lat": entity.get("lat", 0.0),
        "lng": entity.get("lng", 0.0),
        "description": entity.get("description", ""),
        "operating_hours": entity.get("operating_hours", ""),
        "style": entity.get("style", "heritage"),
        "car_parking": entity.get("car_parking", False),
        "motorbike_parking": entity.get("motorbike_parking", True),
        "visit_duration": entity.get("visit_duration_minutes", 60),
        "is_spiritual": entity.get("is_spiritual", False),
        "estimated_cost": est_cost,
        "meal_time": entity.get("meal_time", []),
    })

    # Create Dynasty relationship
    if entity.get("dynasty"):
        session.run("""
            MERGE (d:Dynasty {name: $dynasty})
            WITH d
            MATCH (loc:Location {name: $name})
            MERGE (loc)-[:BUILT_DURING]->(d)
        """, {"dynasty": entity["dynasty"], "name": entity["name"]})

    # Create cultural fact
    if entity.get("historical_significance"):
        session.run("""
            MERGE (fact:CulturalFact {title: $title})
            SET fact.source_document = 'heritage_crawler'
            WITH fact
            MATCH (loc:Location {name: $name})
            MERGE (fact)-[:ABOUT]->(loc)
        """, {
            "title": entity["historical_significance"][:200],
            "name": entity["name"],
        })

    # Create environment tags
    session.run("""
        MERGE (tag:Tag {name: $env})
        WITH tag
        MATCH (loc:Location {name: $name})
        MERGE (loc)-[:HAS_TAG]->(tag)
    """, {"env": entity.get("environment", "Outdoor"), "name": entity["name"]})


def _create_food_node(session, entity: dict):
    """Create a FoodPlace node with culinary metadata."""
    city = _extract_city(entity.get("district", ""))
    ec = entity.get("estimated_cost", {})
    est_cost = ec.get("max", 0) if isinstance(ec, dict) else 0

    session.run("""
        MERGE (food:FoodPlace {name: $name})
        SET food.category = 'food',
            food.city = $city,
            food.district = $district,
            food.environment = $environment,
            food.lat = $lat,
            food.lng = $lng,
            food.cuisine_type = $cuisine_type,
            food.meal_time = $meal_time,
            food.price_range = $price_range,
            food.style = $style,
            food.car_parking = $car_parking,
            food.alley_access = $alley_access,
            food.estimated_cost = $estimated_cost
    """, {
        "name": entity.get("name", ""),
        "city": city,
        "district": entity.get("district", ""),
        "environment": entity.get("environment", "Indoor"),
        "lat": entity.get("lat", 0.0),
        "lng": entity.get("lng", 0.0),
        "cuisine_type": entity.get("cuisine_type", ""),
        "meal_time": entity.get("meal_time", []),
        "price_range": entity.get("price_range", ""),
        "style": entity.get("style", "heritage"),
        "car_parking": entity.get("car_parking", False),
        "alley_access": entity.get("alley_access", False),
        "estimated_cost": est_cost,
    })

    # Tag with environment
    session.run("""
        MERGE (tag:Tag {name: $env})
        WITH tag
        MATCH (food:FoodPlace {name: $name})
        MERGE (food)-[:HAS_TAG]->(tag)
    """, {"env": entity.get("environment", "Indoor"), "name": entity["name"]})


def _create_proximity_relationships(session):
    """Create NEAR relationships between locations in the same district."""
    session.run("""
        MATCH (a), (b)
        WHERE (a:Location OR a:FoodPlace) AND (b:Location OR b:FoodPlace)
          AND a.district = b.district AND a <> b
        MERGE (a)-[:NEAR]->(b)
    """)
    logger.info("🔗 Created proximity (NEAR) relationships")


def _create_weather_relationships(session, data: list):
    """Create weather suitability relationships."""
    for entity in data:
        env = entity.get("environment", "Outdoor")
        name = entity.get("name", "")

        if env == "Indoor":
            session.run("""
                MERGE (w:WeatherCondition {type: 'rainy'})
                WITH w
                MATCH (loc {name: $name})
                MERGE (loc)-[:SUITABLE_FOR]->(w)
            """, {"name": name})
        elif env == "Outdoor" and not entity.get("extreme_weather_block"):
            session.run("""
                MERGE (w:WeatherCondition {type: 'sunny'})
                WITH w
                MATCH (loc {name: $name})
                MERGE (loc)-[:SUITABLE_FOR]->(w)
            """, {"name": name})

    logger.info("🌤️ Created weather suitability relationships")


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
        load_to_neo4j(all_data)
    else:
        logger.warning("No data found. Run static_crawler.py first.")
