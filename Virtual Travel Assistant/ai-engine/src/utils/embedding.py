"""
LUNA AI Engine - Embedding Utilities
Wrapper around sentence-transformers for Vietnamese multilingual embeddings.
"""

from sentence_transformers import SentenceTransformer
from src.config.settings import settings
import logging
import numpy as np

logger = logging.getLogger(__name__)

# Global model instance (lazy loaded)
_model: SentenceTransformer = None


def get_model() -> SentenceTransformer:
    """Get or initialize the embedding model (singleton)."""
    global _model
    if _model is None:
        logger.info(f"📥 Loading embedding model: {settings.embedding_model}")
        _model = SentenceTransformer(settings.embedding_model)
        logger.info(f"✅ Embedding model loaded (dim={_model.get_sentence_embedding_dimension()})")
    return _model


def embed_text(text: str) -> list[float]:
    """Embed a single text string into a 768-dim vector."""
    model = get_model()
    embedding = model.encode(text, normalize_embeddings=True)
    return embedding.tolist()


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed multiple texts into 768-dim vectors (batch)."""
    model = get_model()
    embeddings = model.encode(texts, normalize_embeddings=True, batch_size=32)
    return embeddings.tolist()


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Calculate cosine similarity between two vectors."""
    a = np.array(vec_a)
    b = np.array(vec_b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def embed_persona(persona_data: dict) -> list[float]:
    """
    Create a persona vector from user profile data.
    Combines age, budget, interests, and preferences into a text description,
    then embeds it for vector matching.
    """
    parts = []
    if persona_data.get("age_group"):
        parts.append(f"Nhóm tuổi: {persona_data['age_group']}")
    if persona_data.get("budget_level"):
        parts.append(f"Ngân sách: {persona_data['budget_level']}")
    if persona_data.get("interests"):
        interests_str = ", ".join(persona_data["interests"])
        parts.append(f"Sở thích: {interests_str}")
    if persona_data.get("dietary"):
        dietary_str = ", ".join(persona_data["dietary"])
        parts.append(f"Ăn kiêng: {dietary_str}")
    if persona_data.get("transport"):
        parts.append(f"Phương tiện: {persona_data['transport']}")

    persona_text = ". ".join(parts)
    return embed_text(persona_text)
