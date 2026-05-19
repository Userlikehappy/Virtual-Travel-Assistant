"""
LUNA AI Engine - Trend Agent (Reporter) — Powered by Gemini 3.1 Pro + Native Google Search
Thay vì scrape web thủ công, Agent sử dụng Google Search grounding của Gemini để trực tiếp lấy xu hướng.
"""

import hashlib
import logging
from datetime import datetime
from pydantic import BaseModel, Field

from google import genai
from google.genai import types

from src.config.settings import settings

logger = logging.getLogger(__name__)


class TrendPlace(BaseModel):
    name: str = Field(description="Tên địa điểm (ví dụ: Phở Bà Tùng, Bảng Tàng Chăm)")
    description: str = Field(description="Mô tả ngắn gọn lý do nổi bật hoặc đang hot")
    source_url: str = Field(description="Đường dẫn bài viết/đánh giá nhắc đến địa điểm này", default="")
    category: str = Field(description="Loại hình, chỉ chọn: food, sightseeing, event, nightlife")
    location: str = Field(description="Khu vực/Thành phố")
    score: float = Field(description="Điểm xu hướng từ 0.0 đến 1.0 (1.0 là cực hot)", default=0.8)
    lat: float = Field(description="Vĩ độ (Latitude) chính xác của địa điểm", default=0.0)
    lng: float = Field(description="Kinh độ (Longitude) chính xác của địa điểm", default=0.0)


class TrendResultSchema(BaseModel):
    results: list[TrendPlace] = Field(description="Danh sách các địa điểm xu hướng tìm được")


class TrendResult:
    def __init__(self, query: str, location: str, results: list[TrendPlace], searched_at: str, cached: bool = False):
        self.query = query
        self.location = location
        self.results = results
        self.searched_at = searched_at
        self.cached = cached

    def to_dict(self):
        return {
            "query": self.query,
            "location": self.location,
            "results": [r.model_dump() for r in self.results],
            "searched_at": self.searched_at,
            "cached": self.cached
        }


class TrendAgent:
    """
    Reporter Agent — Tìm kiếm xu hướng thật từ Internet sử dụng Gemini + Google Search Grounding.
    Tìm kiếm và lấy ra Tọa Độ Thực Tế (Real Geolocation). KHÔNG SỬ DỤNG DANH SÁCH HARDCODE.
    """

    SYSTEM_PROMPT = """Bạn là chuyên gia săn xu hướng du lịch (Trend Hunter) tên là LUNA.
Nhiệm vụ: Tìm kiếm và trích xuất các địa điểm siêu hot, quán cafe mới mở, nhà hàng, hoặc sự kiện đang trend tại địa điểm người dùng yêu cầu trên Internet/Mạng xã hội.

BẮT BUỘC TRẢ VỀ JSON:
Bạn phải trả về duy nhất một chuỗi JSON hợp lệ với cấu trúc sau, không có bất kỳ markdown block (như ```json) hay text nào khác ở ngoài:
{
  "results": [
    {
      "name": "Tên địa điểm",
      "description": "Mô tả ngắn gọn lý do hot (tối đa 2 câu)",
      "source_url": "URL nguồn thông tin gốc (rất quan trọng)",
      "category": "food" | "nightlife" | "event" | "sightseeing",
      "location": "Tên thành phố",
      "score": 0.95,
      "lat": 16.0544,
      "lng": 108.2022
    }
  ]
}

QUAN TRỌNG: 
1. Bạn CẦN TÌM CHÍNH XÁC tọa độ (latitude, longitude) của quán đó trên Google Maps và điền vào "lat", "lng". Nếu tuyệt đối không thể xác định được tọa độ thật, hãy để lat=0.0 và lng=0.0. TUYỆT ĐỐI KHÔNG BỊA RA HOẶC TRẢ VỀ TỌA ĐỘ TRUNG TÂM THÀNH PHỐ. Nếu một quán không có tọa độ, nó sẽ bị loại khỏi lịch trình.
2. source_url phải là URL của bài viết, TikTok, hoặc Google Maps review của quán đó.
"""

    def __init__(self, redis_client=None, mongo_client=None):
        self.redis = redis_client
        self.mongo = mongo_client
        
        # Use Application Default Credentials (ADC) implicitly
        self.has_llm = True
        try:
            self.llm = genai.Client(vertexai=True, project=settings.vertex_ai_project, location=settings.vertex_ai_location)
        except Exception as e:
            logger.error(f"Failed to initialize Vertex AI Client: {e}")
            self.has_llm = False
            self.llm = None

    def search(self, query: str, location: str, user_id: str = "", session_id: str = "") -> TrendResult:
        cache_key = f"trend:{hashlib.md5(f'{query}:{location}'.encode()).hexdigest()}"

        # === Check cache ===
        if self.redis:
            try:
                cached = self.redis.get_cache(cache_key)
                if cached:
                    logger.info(f"📦 Cache hit for trend: {query}")
                    # Reconstruct TrendPlace objects from dicts
                    cached_results = [TrendPlace(**r) if isinstance(r, dict) else r for r in cached.get("results", [])]
                    return TrendResult(
                        query=cached.get("query", query),
                        location=cached.get("location", location),
                        results=cached_results,
                        searched_at=cached.get("searched_at", ""),
                        cached=True,
                    )
            except Exception:
                pass

        logger.info(f"🔍 Real Native Search: '{query}' in {location}")
        
        results_list = []
        if self.has_llm and self.llm:
            try:
                # NOTE: response_schema is incompatible with google_search grounding.
                # Use plain text + manual JSON extraction.
                config = types.GenerateContentConfig(
                    system_instruction=self.SYSTEM_PROMPT,
                    temperature=1,
                    top_p=0.95,
                    max_output_tokens=65535,
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    safety_settings=[
                        types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
                        types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
                        types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
                        types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF")
                    ],
                )

                search_query = f"Xu huong mang xa hoi 2026: {query} tai {location}"

                logger.info(f"Sending GenAI request: {search_query}")
                response = self.llm.models.generate_content(
                    model=settings.gemini_model,
                    contents=[search_query],
                    config=config
                )
                logger.info(f"Received GenAI response. Has text: {bool(response.text)}")

                if response.text:
                    import json, re
                    text = response.text.strip()
                    # Strip markdown code block if present
                    match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
                    if match:
                        text = match.group(1)
                    elif not text.startswith('{'):
                        start = text.find('{')
                        if start != -1:
                            text = text[start:]
                    parsed_json = json.loads(text)
                    for item in parsed_json.get("results", []):
                        try:
                            results_list.append(TrendPlace(**item))
                        except Exception:
                            pass
            except Exception as e:
                import traceback
                logger.error(f"❌ Gemini Search failed: {e}")
                traceback.print_exc()
        else:
            logger.warning("⚠️ GOOGLE_CLOUD_API_KEY not set — Cannot perform live Google Search")

        # Fallback if results are empty (either API failed, no text, or JSON parse error)
        if not results_list:
            logger.warning(f"⚠️ Search yielded no results! Using database fallback for {location}")
            # Dùng MongoDB để lấy những quán có trend từ trước
            if self.mongo:
                try:
                    recent_trends = self.mongo.get_recent_trends(location, hours=72)
                    if recent_trends:
                        results_list = [TrendPlace(**r) for r in recent_trends]
                        logger.info(f"✅ Fallback to {len(results_list)} historical trends from MongoDB")
                except Exception as e:
                    logger.error(f"Fallback to Mongo failed: {e}")
            
            # Cuối cùng nếu MongoDB cũng trống, chúng ta trả về list rỗng (KHÔNG HARDCODE LIST TĨNH NỮA)
            if not results_list:
                logger.info("⚠️ Trend fallback also empty. User's query yielded absolutely zero trending items.")
                results_list = []

        result = TrendResult(
            query=query,
            location=location,
            results=results_list,
            searched_at=datetime.utcnow().isoformat(),
        )

        # === Save ===
        if self.redis and results_list:
            try:
                self.redis.set_cache(cache_key, result.to_dict(), ttl_seconds=10800)  # 3 hours
            except Exception:
                pass

        if self.mongo and results_list:
            try:
                self.mongo.save_trend_results(
                    query=query, location=location,
                    results=[r.model_dump() for r in results_list],
                )
            except Exception:
                pass

        logger.info(f"✅ Found {len(results_list)} trending places via Gemini Google Search")
        return result
