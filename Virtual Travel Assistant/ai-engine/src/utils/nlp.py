"""
LUNA AI Engine - Vietnamese NLP Utilities
Text cleaning, tokenization, NER, and sentiment analysis for Vietnamese text.
"""

import re
import logging

logger = logging.getLogger(__name__)

# === TEENCODE / SLANG MAPPING ===
TEENCODE_MAP = {
    "nc": "nước", "vs": "với", "z": "vậy", "ko": "không", "dc": "được",
    "mk": "mình", "bn": "bạn", "cx": "cũng", "ntn": "như thế nào",
    "trc": "trước", "bth": "bình thường", "ns": "nói", "ck": "chồng",
    "vk": "vợ", "nyc": "người yêu cũ", "ny": "người yêu",
    "hqua": "hôm qua", "hni": "hôm nay", "hp": "hạnh phúc",
    "bt": "biết", "đc": "được", "ko": "không", "k": "không",
    "j": "gì", "dth": "điện thoại", "tks": "thanks", "oke": "ok",
    "lun": "luôn", "r": "rồi", "ngon phết": "rất ngon",
    "xỉu": "rất đẹp", "chất": "rất tốt", "chill": "thư giãn",
    "sến": "truyền thống", "deep": "sâu sắc",
}

# === BREAKFAST ITEMS (NEVER serve for dinner) ===
BREAKFAST_DISHES = ["phở", "bún", "bánh mì", "xôi", "cháo", "mì quảng"]

# === DINNER/NIGHTLIFE ITEMS (NEVER serve for breakfast) ===
DINNER_DISHES = ["lẩu", "nướng", "hải sản", "nhậu", "bia", "cocktail"]

# === NEVER BREAKFAST ===
NEVER_BREAKFAST = ["bánh xèo", "lẩu", "nướng", "bia", "cocktail", "nhậu"]

# === HOT WEATHER FOOD ===
HOT_WEATHER_FOOD = ["chè", "kem", "sinh tố", "hải sản", "gỏi", "nước ép", "smoothie"]

# === COLD WEATHER FOOD ===
COLD_WEATHER_FOOD = ["lẩu", "nướng", "bún bò", "cháo", "phở"]

# === ADVENTURE KEYWORDS ===
ADVENTURE_KEYWORDS = ["phượt", "leo núi", "trekking", "đèo", "thác", "mạo hiểm", "cắm trại"]

# === SPIRITUAL / TABOO LOCATIONS ===
SPIRITUAL_KEYWORDS = [
    "chùa", "đền", "miếu", "nghĩa trang", "lăng", "nhà thờ",
    "thánh thất", "đình", "nhà lao", "nhà tù", "tưởng niệm",
]


def clean_text(raw_text: str) -> str:
    """
    Clean Vietnamese text:
    1. Convert teencode to standard Vietnamese
    2. Remove special characters
    3. Normalize whitespace
    """
    text = raw_text.lower().strip()

    # Replace teencode
    for teen, standard in TEENCODE_MAP.items():
        text = re.sub(rf'\b{re.escape(teen)}\b', standard, text)

    # Remove special chars but keep Vietnamese diacritics
    text = re.sub(r'[^\w\sà-ỹÀ-Ỹ.,!?]', '', text)

    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    return text


def tokenize_vietnamese(text: str) -> str:
    """
    Word segmentation for Vietnamese using underthesea.
    Falls back to simple space-split if underthesea is not available.
    """
    try:
        from underthesea import word_tokenize
        return word_tokenize(text, format="text")
    except ImportError:
        logger.warning("underthesea not installed, using simple tokenization")
        return text


def extract_entities(text: str) -> dict:
    """
    Named Entity Recognition for Vietnamese text.
    Extracts location names, food names, and other entities.
    """
    try:
        from underthesea import ner
        entities = ner(text)
        locations = [e[0] for e in entities if e[3] in ('B-LOC', 'I-LOC')]
        persons = [e[0] for e in entities if e[3] in ('B-PER', 'I-PER')]
        organizations = [e[0] for e in entities if e[3] in ('B-ORG', 'I-ORG')]
        return {
            "locations": locations,
            "persons": persons,
            "organizations": organizations,
        }
    except ImportError:
        logger.warning("underthesea not installed, NER unavailable")
        return {"locations": [], "persons": [], "organizations": []}


def analyze_sentiment(text: str) -> str:
    """
    Sentiment analysis for Vietnamese text.
    Returns: 'positive' | 'negative' | 'neutral'
    """
    try:
        from underthesea import sentiment
        return sentiment(text)
    except ImportError:
        logger.warning("underthesea not installed, defaulting to neutral")
        return "neutral"


def classify_query_intent(message: str) -> str:
    """
    Classify user query intent for routing to the correct service.
    Returns: 'historian' | 'reporter' | 'itinerary'
    """
    message_lower = message.lower()

    # === HISTORIAN indicators (history, culture, heritage) ===
    historian_keywords = [
        "lịch sử", "sự tích", "triều", "vua", "nguyễn", "di sản", "văn hóa",
        "truyền thống", "cung đình", "kiến trúc", "cổ", "xưa", "tích",
        "nguồn gốc", "ý nghĩa", "tại sao", "xây dựng năm", "kể cho",
        "câu chuyện", "heritage", "culture", "history",
    ]
    if any(kw in message_lower for kw in historian_keywords):
        return "historian"

    # === REPORTER indicators (trend, hot, new, social) ===
    reporter_keywords = [
        "hot", "trend", "mới", "mới mở", "tối nay", "hôm nay",
        "đang hot", "viral", "check-in", "sống ảo", "quẩy",
        "bar", "pub", "nightlife", "tiktok", "facebook",
        "xu hướng", "trending", "nổi tiếng", "đông khách",
    ]
    if any(kw in message_lower for kw in reporter_keywords):
        return "reporter"

    # === ITINERARY indicators (plan, schedule, trip) ===
    itinerary_keywords = [
        "lịch trình", "kế hoạch", "ngày", "đêm", "tour", "chuyến đi",
        "tham quan", "đi đâu", "nên đi", "gợi ý", "sắp xếp",
        "lên kế hoạch", "itinerary", "plan", "schedule",
    ]
    if any(kw in message_lower for kw in itinerary_keywords):
        return "itinerary"

    # Default to historian (safest for Q&A)
    return "historian"


def is_spiritual_location(name: str) -> bool:
    """Check if a location is a spiritual/sacred place (for Taboo Filter)."""
    name_lower = name.lower()
    return any(kw in name_lower for kw in SPIRITUAL_KEYWORDS)


def auto_label_entity(entity: dict) -> dict:
    """
    Auto-label an entity with metadata based on content analysis.
    Applied during data pipeline processing.
    """
    # === PHÂN LOẠI HERITAGE vs TRENDING ===
    if entity.get("trend_source"):
        entity["style"] = "trending"
    elif entity.get("historical_significance") or entity.get("heritage_story"):
        entity["style"] = "heritage"

    # === GÁN MEAL_TIME CHO ĐỒ ĂN ===
    dish = entity.get("dish_name", "").lower()
    if any(d in dish for d in NEVER_BREAKFAST):
        meal_times = entity.get("meal_time", [])
        entity["meal_time"] = [t for t in meal_times if t != "breakfast"]

    # === GÁN THỜI TIẾT CHO ĐỒ ĂN ===
    if any(f in dish for f in HOT_WEATHER_FOOD):
        entity["weather_ideal"] = ["hot", "sunny"]
        entity["hot_weather_food"] = True
    elif any(f in dish for f in COLD_WEATHER_FOOD):
        entity["weather_ideal"] = ["cold", "rainy"]

    # === GÁN ADVENTURE LEVEL ===
    name = entity.get("name", "").lower()
    if any(k in name for k in ADVENTURE_KEYWORDS):
        entity["adventure_level"] = "extreme"
        entity["target_age"] = ["gen_z", "millennial"]
        entity["family_friendly"] = False

    # === GÁN SPIRITUAL TAG ===
    if is_spiritual_location(entity.get("name", "")):
        entity["is_spiritual"] = True
        entity["best_time_of_day"] = ["morning", "afternoon"]

    return entity
