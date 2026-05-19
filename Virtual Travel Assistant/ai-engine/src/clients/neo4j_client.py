"""
LUNA AI Engine - Neo4j Client
Manages connection to Neo4j Knowledge Graph for entity relationships and graph traversal.
"""

from neo4j import GraphDatabase, AsyncGraphDatabase
import re
from src.config.settings import settings
import logging

logger = logging.getLogger(__name__)


class Neo4jClient:
    """Neo4j driver wrapper for Knowledge Graph operations."""

    def __init__(self):
        self._driver = None

    def connect(self):
        """Establish connection to Neo4j and ensure required indexes exist."""
        try:
            self._driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
            )
            self._driver.verify_connectivity()
            logger.info(f"✅ Connected to Neo4j at {settings.neo4j_uri}")
            self._ensure_indexes()
        except Exception as e:
            logger.error(f"❌ Failed to connect to Neo4j: {e}")
            raise

    def _ensure_indexes(self):
        """Create required indexes if they don't exist."""
        indexes = [
            # Fulltext index used by traverse_for_context RAG pipeline
            """CREATE FULLTEXT INDEX locationIndex IF NOT EXISTS
               FOR (n:Location)
               ON EACH [n.name, n.description, n.category, n.city, n.district]""",
            # Regular index for city-based lookups
            "CREATE INDEX location_city IF NOT EXISTS FOR (n:Location) ON (n.city)",
            "CREATE INDEX location_name IF NOT EXISTS FOR (n:Location) ON (n.name)",
            "CREATE INDEX location_category IF NOT EXISTS FOR (n:Location) ON (n.category)",
        ]
        for cypher in indexes:
            try:
                with self._driver.session() as session:
                    session.run(cypher)
            except Exception as e:
                logger.warning(f"Index creation skipped (may already exist): {e}")
        logger.info("✅ Neo4j indexes verified")

    def close(self):
        """Close Neo4j connection."""
        if self._driver:
            self._driver.close()
            logger.info("Neo4j connection closed")

    def query(self, cypher: str, parameters: dict = None) -> list:
        """Execute a Cypher query and return results as list of dicts."""
        try:
            with self._driver.session() as session:
                result = session.run(cypher, parameters or {})
                return [record.data() for record in result]
        except Exception as e:
            logger.error(f"Neo4j query failed: {e}")
            return []

    def find_nearby_locations(self, location_name: str, limit: int = 5) -> list:
        """Find locations near a given location via NEAR relationship, or simply locations in that city."""
        city_match = ""
        if "Đà Nẵng" in location_name or "Da Nang" in location_name: city_match = "Đà Nẵng"
        elif "Huế" in location_name or "Hue" in location_name: city_match = "Huế"
        elif "Hội An" in location_name or "Hoi An" in location_name: city_match = "Hội An"

        if city_match:
            # Include both Location and FoodPlace nodes so food slots get real lat/lng
            cypher = """
            MATCH (loc)
            WHERE (loc:Location OR loc:FoodPlace) AND loc.city CONTAINS $city
            RETURN loc.name AS name, loc.category AS category, loc.environment AS environment,
                   loc.lat AS lat, loc.lng AS lng, loc.meal_time AS meal_time,
                   loc.operating_hours AS operating_hours, loc.estimated_cost AS estimated_cost,
                   loc.style AS style, loc.district AS district
            LIMIT $limit
            """
            res = self.query(cypher, {"city": city_match, "limit": limit})
            if res:
                return res

        # Fallback to nearest neighbors
        cypher = """
        MATCH (loc)-[:NEAR]-(nearby)
        WHERE (loc:Location OR loc:FoodPlace) AND loc.name = $name
        RETURN nearby.name AS name, nearby.category AS category,
               nearby.environment AS environment, nearby.lat AS lat, nearby.lng AS lng,
               nearby.meal_time AS meal_time, nearby.operating_hours AS operating_hours,
               nearby.estimated_cost AS estimated_cost, nearby.style AS style,
               nearby.district AS district
        LIMIT $limit
        """
        return self.query(cypher, {"name": location_name, "limit": limit})

    def find_indoor_food_near(self, location_name: str, meal_time: str = "lunch", limit: int = 5) -> list:
        """Find indoor food places near a location, filtered by meal time."""
        cypher = """
        MATCH (loc:Location {name: $name})-[:NEAR]-(food:FoodPlace)-[:HAS_TAG]-(tag:Tag)
        WHERE tag.name IN ['Indoor', 'Sheltered']
          AND food.meal_time = $meal_time
        RETURN food.name AS name, food.price_range AS price_range,
               food.cuisine_type AS cuisine_type, food.car_parking AS car_parking
        ORDER BY food.price_range ASC
        LIMIT $limit
        """
        return self.query(cypher, {"name": location_name, "meal_time": meal_time, "limit": limit})

    def get_cultural_facts(self, location_name: str) -> list:
        """Get cultural facts about a location via ABOUT relationship."""
        cypher = """
        MATCH (fact:CulturalFact)-[:ABOUT]->(loc:Location {name: $name})
        OPTIONAL MATCH (fact)-[:HAPPENED_IN]->(dynasty:Dynasty)
        RETURN fact.title AS title, fact.dynasty AS dynasty,
               fact.year AS year, fact.source_document AS source,
               fact.confidence_score AS confidence
        ORDER BY fact.year ASC
        """
        return self.query(cypher, {"name": location_name})

    def get_weather_suitable_locations(self, weather_type: str, environment: str = "Indoor", limit: int = 10) -> list:
        """Find locations suitable for given weather conditions."""
        cypher = """
        MATCH (loc)-[:SUITABLE_FOR]->(weather:WeatherCondition {type: $weather_type})
        WHERE loc.environment = $environment OR loc.environment = 'Sheltered'
        RETURN loc.name AS name, loc.category AS category,
               loc.lat AS lat, loc.lng AS lng,
               loc.operating_hours AS operating_hours
        LIMIT $limit
        """
        return self.query(cypher, {
            "weather_type": weather_type,
            "environment": environment,
            "limit": limit,
        })

    def traverse_for_context(self, query_text: str, max_depth: int = 2) -> list:
        """General graph traversal for RAG context enrichment."""
        # Sanitize query to prevent Lucene lexical errors
        sanitized_query = re.sub(r'[^\w\s]', ' ', query_text)
        sanitized_query = ' '.join(sanitized_query.split())

        if not sanitized_query:
            return []

        # Try fulltext index first (fast path)
        try:
            cypher = f"""
            CALL db.index.fulltext.queryNodes('locationIndex', $query)
            YIELD node, score
            WITH node, score
            LIMIT 5
            OPTIONAL MATCH path = (node)-[*1..{max_depth}]-(related)
            RETURN node.name AS entity, labels(node) AS labels,
                   collect(DISTINCT {{
                       related: related.name,
                       type: type(last(relationships(path)))
                   }}) AS connections,
                   score
            """
            with self._driver.session() as session:
                result = session.run(cypher, {"query": sanitized_query})
                return [record.data() for record in result]
        except Exception as e:
            if "no such fulltext schema index" in str(e).lower() or "locationIndex" in str(e):
                logger.warning("locationIndex not ready yet — falling back to CONTAINS search")
                # Fallback: plain CONTAINS match (slower but works without index)
                fallback = f"""
                MATCH (node:Location)
                WHERE toLower(node.name) CONTAINS toLower($query)
                   OR toLower(node.category) CONTAINS toLower($query)
                   OR toLower(coalesce(node.city, '')) CONTAINS toLower($query)
                WITH node, 1.0 AS score
                LIMIT 5
                OPTIONAL MATCH path = (node)-[*1..{max_depth}]-(related)
                RETURN node.name AS entity, labels(node) AS labels,
                       collect(DISTINCT {{
                           related: related.name,
                           type: type(last(relationships(path)))
                       }}) AS connections,
                       score
                """
                return self.query(fallback, {"query": sanitized_query.split()[0] if sanitized_query else ""})
            logger.error(f"Neo4j traverse_for_context failed: {e}")
            return []
