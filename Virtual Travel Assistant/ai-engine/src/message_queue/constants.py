"""
LUNA AI Engine - Message Queue Constants
Defines all RabbitMQ exchanges, queues, and routing keys.
"""

# === EXCHANGES ===
MQ_EXCHANGES = {
    "SEARCH": "luna.search",
    "PIPELINE": "luna.pipeline",
    "WEATHER": "luna.weather",
    "DLQ": "luna.dlq",
}

# === QUEUES ===
MQ_QUEUES = {
    "TREND_SEARCH_TASKS": "trend.search.tasks",
    "TREND_SEARCH_RESULTS": "trend.search.results",
    "DATA_CRAWL_TASKS": "data.crawl.tasks",
    "DATA_EMBEDDING_TASKS": "data.embedding.tasks",
    "WEATHER_ALERTS": "weather.alerts",
    "DEAD_LETTER": "dead.letter.queue",
}

# === ROUTING KEYS ===
MQ_ROUTING_KEYS = {
    "TREND_TASK": "trend.task",
    "TREND_RESULT": "trend.result",
    "CRAWL_TASK": "crawl.task",
    "EMBEDDING_TASK": "embedding.task",
    "WEATHER_ALERT": "weather.alert",
}

# === RETRY CONFIG ===
MQ_MAX_RETRIES = 3
MQ_RETRY_DELAY_MS = 5000
