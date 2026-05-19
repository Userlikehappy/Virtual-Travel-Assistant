"""
LUNA AI Engine - Agent Team
Multi-agent pipeline for intelligent, real-data-driven itinerary generation.

Pipeline:
  ResearchAgent → PlannerAgent

ResearchAgent: Gemini + Google Search grounding → tìm địa điểm thật, toạ độ thật
PlannerAgent:  Gemini reasoning → xây lịch trình theo zone địa lý, hợp lý, hấp dẫn

Fallback: nếu agent team thất bại → dùng pipeline rule-based cũ (Neo4j + Milvus + ScoringEngine)
"""

import json
import logging
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field
from google import genai
from google.genai import types

from src.config.settings import settings
from src.services.itinerary_logic import (
    ItineraryResult, DayPlanResult, TimeSlotResult, DEFAULT_SLOTS
)

logger = logging.getLogger(__name__)


# ─── Pydantic schemas for structured Gemini output ───────────────────────────

class ResearchLocation(BaseModel):
    name: str = Field(description="Tên đầy đủ của địa điểm")
    category: str = Field(description="Loại: food, sightseeing, beach, museum, entertainment, spiritual, culture, cafe, nightlife")
    environment: str = Field(description="Môi trường: Indoor, Outdoor, hoặc Sheltered")
    address: str = Field(description="Địa chỉ đầy đủ")
    district: str = Field(description="Quận/Phường (ví dụ: Hải Châu, Sơn Trà, Ngũ Hành Sơn)")
    lat: float = Field(description="Vĩ độ chính xác từ Google Maps. Nếu không biết để 0.0")
    lng: float = Field(description="Kinh độ chính xác từ Google Maps. Nếu không biết để 0.0")
    operating_hours: str = Field(description="Giờ mở cửa, ví dụ: 07:00-22:00. Để trống nếu không biết", default="")
    estimated_cost: int = Field(description="Chi phí ước tính mỗi người (VNĐ)", default=0)
    description: str = Field(description="Mô tả thực tế 1-2 câu: nổi bật gì, nên ăn/xem gì")
    meal_time: list[str] = Field(description="Các bữa phù hợp nếu là quán ăn: breakfast, lunch, dinner, snack. Để [] nếu không phải quán ăn", default=[])
    why_visit: str = Field(description="Lý do nên đến: 1 câu ngắn gọn, cụ thể (ví dụ: 'Cơm gà vàng ươm chuẩn vị Hội An, xếp hàng từ 11h')", default="")
    source_url: str = Field(description="URL nguồn (Google Maps, TripAdvisor, blog review...)", default="")


class ResearchResultSchema(BaseModel):
    locations: list[ResearchLocation] = Field(description="Danh sách địa điểm tìm được")


class PlannedSlot(BaseModel):
    time_range: str = Field(description="Khung giờ, ví dụ: 07:00-08:30")
    slot_type: str = Field(description="Loại slot: breakfast, sightseeing, lunch, dinner, snack, nightlife")
    place_name: str = Field(description="Tên địa điểm CHÍNH XÁC như trong danh sách research")
    note: str = Field(description="Mô tả gợi cảm 1-2 câu + tip insider cụ thể cho slot này")
    travel_note: str = Field(description="Ghi chú di chuyển từ slot trước (ví dụ: '10 phút đi xe máy về hướng biển')", default="")


class PlannedDay(BaseModel):
    day_number: int
    day_theme: str = Field(description="Chủ đề ngày (ví dụ: 'Khám phá Sơn Trà & bờ biển')")
    geographic_zone: str = Field(description="Khu vực chính của ngày (ví dụ: 'Sơn Trà - Mỹ Khê')")
    slots: list[PlannedSlot]


class PlanResultSchema(BaseModel):
    days: list[PlannedDay]
    trip_summary: str = Field(description="Tổng kết chuyến đi 1-2 câu, highlight điểm đặc sắc nhất")


# ─── ResearchAgent ────────────────────────────────────────────────────────────

class ResearchAgent:
    """
    Dùng Gemini + Google Search Grounding để tìm địa điểm THẬT, toạ độ THẬT.
    Không dùng danh sách hardcode. Tìm kiếm live từ Internet.
    """

    SYSTEM_PROMPT = """Bạn là chuyên gia nghiên cứu địa điểm du lịch Việt Nam tên LUNA Research.

NHIỆM VỤ: Tìm kiếm và trích xuất danh sách địa điểm THỰC TẾ, ĐANG HOẠT ĐỘNG tại điểm đến được yêu cầu.
Ưu tiên: nhà hàng ngon, địa điểm check-in nổi tiếng, quán cafe đang hot, địa điểm văn hoá/lịch sử đặc trưng.

QUY TẮC BẮT BUỘC:
1. CHỈ trả về địa điểm CÓ THẬT, đang hoạt động năm 2025-2026.
2. Toạ độ lat/lng PHẢI lấy từ Google Maps, KHÔNG bịa. Nếu không tìm được toạ độ chính xác, để 0.0.
3. estimated_cost là chi phí ước tính mỗi người (VNĐ) theo giá thực tế hiện tại. VÍ DỤ: quán phở bình dân=35000, nhà hàng hải sản=200000, vé tham quan=50000. KHÔNG ĐỂ 0.
4. description và why_visit phải CỤ THỂ: nêu món đặc trưng, điểm nổi bật thực sự.
5. Phân bổ đa dạng: cần đủ cho 1 ngày đầy đủ: sáng (ăn sáng + tham quan), trưa (ăn trưa), chiều (tham quan), tối (ăn tối + hoạt động).
6. Tập trung địa điểm trong bán kính 15km của trung tâm thành phố yêu cầu.

KHÔNG ĐƯỢC:
- Bịa địa điểm không có thật
- Trả về địa điểm đã đóng cửa
- Dùng toạ độ trung tâm thành phố thay cho toạ độ thực của địa điểm
- Trả về địa điểm ở tỉnh/thành khác

TÊN ĐỊA ĐIỂM: Luôn dùng tên tiếng Việt có dấu đầy đủ (ví dụ: "Cầu Sông Hàn", "Bãi biển Mỹ Khê", không phải "Song Han Bridge").

BẮT BUỘC TRẢ VỀ JSON THUẦN với cấu trúc SAU (không có markdown block, không text thừa):
{
  "locations": [
    {
      "name": "Tên tiếng Việt đầy đủ dấu",
      "category": "food|sightseeing|beach|museum|entertainment|spiritual|culture|cafe|nightlife",
      "environment": "Indoor|Outdoor|Sheltered",
      "address": "Địa chỉ đầy đủ",
      "district": "Quận/Phường",
      "lat": 16.0544,
      "lng": 108.2022,
      "operating_hours": "07:00-22:00",
      "estimated_cost": 50000,
      "description": "Mô tả 1-2 câu cụ thể",
      "meal_time": ["breakfast"],
      "why_visit": "Lý do 1 câu ngắn gọn cụ thể",
      "source_url": ""
    }
  ]
}"""

    def __init__(self, llm_client):
        self.llm = llm_client

    def research(self, destination: str, preferences: list, num_days: int) -> list[dict]:
        """
        Tìm địa điểm thật bằng Gemini + Google Search.
        Trả về list dicts sẵn sàng dùng cho PlannerAgent.
        """
        pref_str = ", ".join(preferences) if preferences else "khám phá văn hoá, ẩm thực"
        needed = max(14, num_days * 5)  # Đủ cho mọi slot, không quá nhiều để tránh timeout

        query = (
            f"Tìm {needed} địa điểm du lịch, ăn uống, tham quan TỐT NHẤT và ĐANG HOẠT ĐỘNG tại {destination} năm 2025-2026. "
            f"Sở thích khách: {pref_str}. "
            f"Cần đủ: quán ăn sáng, trưa, tối; địa điểm tham quan buổi sáng và chiều; quán cafe; hoạt động buổi tối. "
            f"Ưu tiên địa điểm nổi tiếng, đánh giá cao, ĐANG HOẠT ĐỘNG, có toạ độ chính xác trên Google Maps."
        )

        logger.info(f"🔍 ResearchAgent searching: {destination} ({num_days} days, {needed} locations needed)")

        try:
            # NOTE: response_schema (controlled generation) is incompatible with google_search.
            # Use plain text response + manual JSON extraction instead.
            config = types.GenerateContentConfig(
                system_instruction=self.SYSTEM_PROMPT,
                temperature=1,
                top_p=0.95,
                max_output_tokens=65535,
                tools=[types.Tool(google_search=types.GoogleSearch())],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    maximum_remote_calls=3,  # limit to 3 search calls to avoid 2+ min waits
                ),
                safety_settings=[
                    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
                    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
                    types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
                    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF"),
                ],
            )

            response = self.llm.models.generate_content(
                model=settings.gemini_model,
                contents=[query],
                config=config,
            )

            if not response.text:
                logger.warning("ResearchAgent: empty response from Gemini")
                return []

            import re
            text = response.text.strip()
            # Strip markdown code block if present
            match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
            if match:
                text = match.group(1)
            elif not text.startswith('{'):
                start = text.find('{')
                if start != -1:
                    text = text[start:]
            parsed = json.loads(text)
            locations = parsed.get("locations", [])
            locations = [loc for loc in locations if loc.get("name", "").strip()]
            logger.info(f"✅ ResearchAgent found {len(locations)} locations for {destination}")
            return locations

        except Exception as e:
            logger.error(f"❌ ResearchAgent failed: {e}")
            return []


# ─── PlannerAgent ─────────────────────────────────────────────────────────────

class PlannerAgent:
    """
    Dùng Gemini reasoning để xây lịch trình tối ưu từ danh sách địa điểm đã research.
    Tập trung vào: zone địa lý (không di chuyển xa), đa dạng trải nghiệm, narrative hấp dẫn.
    """

    SYSTEM_PROMPT = """Bạn là chuyên gia lập lịch trình du lịch LUNA Planner.

NHIỆM VỤ: Tạo lịch trình {num_days} ngày cho {destination} từ danh sách địa điểm được cung cấp.

NGUYÊN TẮC LẬP LỊCH:
1. **Zone địa lý**: Gom các địa điểm cùng khu vực vào cùng buổi (sáng/chiều). Tránh di chuyển cắt ngang thành phố giữa các slot liên tiếp. Ví dụ: ăn sáng gần Hải Châu → tham quan Bảo tàng Chăm (cùng khu) → ăn trưa gần đó.
2. **Đa dạng ẩm thực**: Không lặp lại cùng loại món (không 2 tô bún liền, không 2 quán hải sản liền).
3. **Nhịp điệu ngày**: Sáng sớm = ăn nhẹ + địa điểm mát mẻ → Tham quan buổi sáng → Ăn trưa thực chất → Nghỉ/cafe nhẹ → Tham quan chiều → Ăn tối đặc sắc → Hoạt động tối thú vị.
4. **Chủ đề ngày**: Mỗi ngày có theme riêng (ngày 1: trung tâm & bảo tàng, ngày 2: biển & núi, ngày 3: ẩm thực & phố cổ...).
5. **Note thực tế**: Mỗi slot phải có note CỤ THỂ: gợi ý món gọi, góc chụp ảnh đẹp, thời điểm lý tưởng, tip local.
6. **Chỉ dùng địa điểm trong danh sách**: place_name phải CHÍNH XÁC như tên trong danh sách.
7. **Phân bổ hết ngày**: Mỗi ngày cần đủ 7 slot: breakfast (07:00-08:30), sightseeing buổi sáng (09:00-11:30), lunch (12:00-13:30), sightseeing chiều (14:00-16:30), snack/cafe (16:30-17:30), dinner (18:00-19:30), hoạt động tối (20:00-22:00).

CHẤT LƯỢNG NOTE:
✅ Tốt: "Gọi tô bún chả cá đặc biệt có thêm chả hấp — nước dùng ngọt thanh từ cá tươi, người địa phương ăn từ 6h sáng"
✅ Tốt: "Chụp ảnh góc tháp Phước Duyên ngược sáng buổi sớm trước 8h, ánh vàng rất đẹp"
❌ Tệ: "Đây là địa điểm đẹp, nên ghé thăm"

TRẢ VỀ JSON THUẦN, KHÔNG MARKDOWN BLOCK."""

    def __init__(self, llm_client):
        self.llm = llm_client

    def plan(
        self,
        destination: str,
        num_days: int,
        locations: list[dict],
        preferences: list,
        weather: str,
        budget_level: str,
        transport_mode: str,
    ) -> Optional[PlanResultSchema]:
        """
        Tạo lịch trình tối ưu từ danh sách địa điểm.
        """
        if not locations:
            return None

        pref_str = ", ".join(preferences) if preferences else "khám phá văn hoá, ẩm thực"
        weather_note = {
            "rainy": "⚠️ Thời tiết có mưa — ưu tiên địa điểm trong nhà (museum, cafe, nhà hàng). Hạn chế outdoor.",
            "stormy": "🌩️ Bão — CHỈ gợi ý địa điểm trong nhà có mái che. Tuyệt đối không đi biển/núi.",
            "cloudy": "☁️ Trời mát — tốt cho tham quan outdoor và đi bộ.",
            "sunny": "☀️ Nắng đẹp — lý tưởng cho biển, núi, tham quan ngoài trời.",
        }.get(weather, "☀️ Nắng đẹp")

        budget_note = {
            "low": "Ngân sách tiết kiệm — ưu tiên quán bình dân, tránh nhà hàng sang.",
            "medium": "Ngân sách trung bình — cân bằng giữa trải nghiệm và chi phí.",
            "high": "Ngân sách thoải mái — có thể gợi ý nhà hàng view đẹp, trải nghiệm premium.",
        }.get(budget_level, "Ngân sách trung bình")

        # Format location list for the prompt
        loc_lines = []
        for i, loc in enumerate(locations, 1):
            meals = f", meal_time: {loc.get('meal_time', [])}" if loc.get("meal_time") else ""
            coords = f"[{loc.get('lat', 0.0):.4f}, {loc.get('lng', 0.0):.4f}]"
            loc_lines.append(
                f"{i}. [{loc.get('category','?')}] {loc.get('name','')} "
                f"({loc.get('district','?')}, {coords}{meals}, ~{loc.get('estimated_cost',0):,}đ) "
                f"| Giờ: {loc.get('operating_hours','?')} "
                f"| {loc.get('why_visit', loc.get('description',''))}"
            )
        locations_text = "\n".join(loc_lines)

        system = self.SYSTEM_PROMPT.replace("{num_days}", str(num_days)).replace("{destination}", destination)

        query = f"""Lập lịch trình {num_days} ngày tại {destination}.

THÔNG TIN CHUYẾN ĐI:
- Điểm đến: {destination}
- Số ngày: {num_days}
- Sở thích: {pref_str}
- Thời tiết: {weather_note}
- Ngân sách: {budget_note}
- Phương tiện: {transport_mode}

DANH SÁCH ĐỊA ĐIỂM CÓ THỂ DÙNG:
{locations_text}

Hãy xây lịch trình {num_days} ngày đầy đủ, thú vị, hợp lý về địa lý.
Mỗi ngày phải có đủ 7 slot. Chỉ dùng địa điểm trong danh sách trên."""

        logger.info(f"🗺️ PlannerAgent planning {num_days} days for {destination} ({len(locations)} locations available)")

        try:
            config = types.GenerateContentConfig(
                system_instruction=system,
                temperature=1,
                top_p=0.95,
                max_output_tokens=65535,
                response_mime_type="application/json",
                response_schema=PlanResultSchema,
                safety_settings=[
                    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
                    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
                    types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
                    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF"),
                ],
            )

            response = self.llm.models.generate_content(
                model=settings.gemini_model,
                contents=[query],
                config=config,
            )

            if not response.text:
                logger.warning("PlannerAgent: empty response from Gemini")
                return None

            parsed = json.loads(response.text)
            plan = PlanResultSchema(**parsed)
            logger.info(f"✅ PlannerAgent created {len(plan.days)}-day plan for {destination}")
            return plan

        except Exception as e:
            logger.error(f"❌ PlannerAgent failed: {e}")
            return None


# ─── AgentTeam Orchestrator ───────────────────────────────────────────────────

class AgentTeam:
    """
    Orchestrates ResearchAgent → PlannerAgent to generate high-quality itineraries.

    Replaces the static Neo4j/Milvus rule-based pipeline with a live, Gemini-powered pipeline.
    Falls back to rule-based pipeline if Gemini is unavailable or fails.
    """

    def __init__(self):
        self._available = False
        self.research_agent = None
        self.planner_agent = None

        try:
            llm = genai.Client(
                vertexai=True,
                project=settings.vertex_ai_project,
                location=settings.vertex_ai_location,
            )
            self.research_agent = ResearchAgent(llm)
            self.planner_agent = PlannerAgent(llm)
            self._available = True
            logger.info("✅ AgentTeam initialized (ResearchAgent + PlannerAgent)")
        except Exception as e:
            logger.warning(f"⚠️ AgentTeam unavailable (Vertex AI not configured): {e}")

    @property
    def available(self) -> bool:
        return self._available

    def generate(
        self,
        destination: str,
        num_days: int,
        preferences: list,
        weather_forecast: str = "sunny",
        budget_level: str = "medium",
        transport_mode: str = "motorbike",
    ) -> ItineraryResult:
        """
        Full agent pipeline: Research → Plan → Convert to ItineraryResult.
        Raises RuntimeError if pipeline fails (caller should catch and fall back).
        """
        if not self._available:
            raise RuntimeError("AgentTeam not available")

        # Step 1: Research
        locations = self.research_agent.research(destination, preferences, num_days)
        if not locations:
            raise RuntimeError("ResearchAgent returned no locations")

        # Index locations by name for fast lookup (exact + case-insensitive + fuzzy)
        loc_by_name: dict[str, dict] = {
            loc["name"].strip(): loc for loc in locations if loc.get("name")
        }
        _loc_lower: dict[str, dict] = {k.lower(): v for k, v in loc_by_name.items()}

        def _lookup(name: str) -> dict:
            n = name.strip()
            # 1. Exact match
            if n in loc_by_name:
                return loc_by_name[n]
            # 2. Case-insensitive
            if n.lower() in _loc_lower:
                return _loc_lower[n.lower()]
            # 3. Fuzzy (difflib)
            from difflib import get_close_matches
            keys = list(loc_by_name.keys())
            hits = get_close_matches(n, keys, n=1, cutoff=0.6)
            if hits:
                logger.debug(f"Fuzzy match: '{n}' → '{hits[0]}'")
                return loc_by_name[hits[0]]
            return {}

        # Step 2: Plan
        plan = self.planner_agent.plan(
            destination=destination,
            num_days=num_days,
            locations=locations,
            preferences=preferences,
            weather=weather_forecast,
            budget_level=budget_level,
            transport_mode=transport_mode,
        )
        if not plan:
            raise RuntimeError("PlannerAgent returned no plan")

        # Step 3: Convert PlanResultSchema → ItineraryResult
        result = ItineraryResult()
        total_cost = 0.0

        for day_plan in plan.days:
            day_result = DayPlanResult(day_number=day_plan.day_number)
            day_cost = 0.0
            first_slot = True

            for slot in day_plan.slots:
                # Look up real metadata from research results
                meta = _lookup(slot.place_name)

                lat = float(meta.get("lat") or 0.0)
                lng = float(meta.get("lng") or 0.0)
                # estimated_cost field name fallback (Gemini sometimes uses 'cost' or 'price')
                cost = float(
                    meta.get("estimated_cost")
                    or meta.get("cost")
                    or meta.get("price")
                    or meta.get("ticket_price")
                    or 0
                )

                note = slot.note or ""
                if slot.travel_note:
                    note = f"{note} | 🚗 {slot.travel_note}" if note else f"🚗 {slot.travel_note}"

                # Encode day theme into the first slot's note so it surfaces in the UI
                if first_slot and day_plan.day_theme:
                    note = f"📍 {day_plan.day_theme} | {note}" if note else f"📍 {day_plan.day_theme}"
                    first_slot = False

                slot_result = TimeSlotResult(
                    time_range=slot.time_range,
                    place_name=slot.place_name,
                    category=meta.get("category", slot.slot_type),
                    environment=meta.get("environment", "Outdoor"),
                    score=0.9,  # Agent-selected = high confidence
                    note=note,
                    lat=lat,
                    lng=lng,
                    estimated_cost=cost,
                    google_maps_url=meta.get("source_url", ""),
                    facebook_url="",
                    tripadvisor_url="",
                    image_url="",
                )
                day_result.slots.append(slot_result)
                day_cost += cost

            day_result.day_cost = day_cost
            result.days.append(day_result)
            total_cost += day_cost

        result.total_estimated_cost = total_cost
        result.estimated_route_km = self._estimate_route_km(result)

        logger.info(
            f"✅ AgentTeam complete: {num_days} days, "
            f"est. {total_cost:,.0f}đ, route ~{result.estimated_route_km}km"
        )
        return result

    def _estimate_route_km(self, result: ItineraryResult) -> float:
        from src.utils.geo import haversine_distance
        total = 0.0
        for day_plan in result.days:
            prev = None
            for s in day_plan.slots:
                lat, lng = float(s.lat or 0), float(s.lng or 0)
                if lat == 0.0 and lng == 0.0:
                    continue
                if prev:
                    total += haversine_distance(prev[0], prev[1], lat, lng)
                prev = (lat, lng)
        return round(total, 2)
