"""
LUNA AI Engine - Configuration Module
Pydantic BaseSettings auto-loads from .env file and validates all required keys.
"""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Centralized configuration for LUNA AI Engine."""

    # === gRPC Server ===
    grpc_host: str = "0.0.0.0"
    grpc_port: int = 50051
    grpc_max_workers: int = 10

    # === AI / LLM (Gemini Vertex AI) ===
    google_cloud_api_key: str = ""
    gemini_model: str = "gemini-2.5-pro"
    tavily_api_key: str = ""
    vertex_ai_project: str = "lunatv-488810"
    vertex_ai_location: str = "global"

    # === Neo4j (Knowledge Graph) ===
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"

    # === Milvus (Vector DB) ===
    milvus_uri: str = "http://localhost:19530"

    # === MongoDB (Chat History) ===
    mongodb_uri: str = "mongodb://luna_mongo:luna_mongo_pass@localhost:27017"
    mongodb_db_name: str = "luna_chat"

    # === RabbitMQ (Message Queue) ===
    rabbitmq_url: str = "amqp://luna_mq:luna_mq_pass@localhost:5672"

    # === Redis (Cache) ===
    redis_url: str = "redis://localhost:6379"

    # === Embedding Model ===
    embedding_model: str = "paraphrase-multilingual-mpnet-base-v2"

    # === Scoring Weights (Default - Sunny weather) ===
    w_persona: float = 0.35
    w_weather: float = 0.10
    w_trend: float = 0.25
    w_culinary: float = 0.15
    w_logistics: float = 0.15

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    """Singleton pattern for settings. Cached after first load."""
    return Settings()


# Convenience: import directly
settings = get_settings()
