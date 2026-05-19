"""
LUNA AI Engine - Itinerary Logic
Adaptive Itinerary Generator: TSP optimization, time slot allocation, weather re-route.
"""

import math
import re
import random
import hashlib
import logging
from typing import Optional, Tuple
from datetime import datetime, time, timedelta
from dataclasses import dataclass, field

from src.config.settings import settings
from src.clients.neo4j_client import Neo4jClient
from src.clients.milvus_client import MilvusClient
from src.services.scoring_engine import ScoringEngine, ScoringContext, LocationScore
from src.utils.embedding import embed_text
from src.utils.geo import haversine_distance

logger = logging.getLogger(__name__)

def is_open_during_slot(operating_hours: str, slot_start: str, slot_end: str) -> bool:
    """
    Parse operating hours string and check if it overlaps with the requested time slot.
    Returns True if open, or if parsing fails (fallback safe).
    """
    if not operating_hours or not isinstance(operating_hours, str):
        return True
        
    lower_hours = operating_hours.lower()
    if "tự do" in lower_hours or "cả ngày" in lower_hours or "24/24" in lower_hours or "24/7" in lower_hours:
        return True
        
    try:
        # Extract HH:MM patterns
        times = re.findall(r'\d{1,2}:\d{2}', operating_hours)
        if len(times) >= 2:
            open_h, open_m = map(int, times[0].split(':'))
            close_h, close_m = map(int, times[1].split(':'))
            slot_start_h, slot_start_m = map(int, slot_start.split(':'))
            slot_end_h, slot_end_m = map(int, slot_end.split(':'))
            
            open_time = open_h + open_m / 60.0
            close_time = close_h + close_m / 60.0
            req_start = slot_start_h + slot_start_m / 60.0
            req_end = slot_end_h + slot_end_m / 60.0
            
            if close_time < open_time: # e.g. 15:00 - 02:00 (closes next day)
                close_time += 24.0
                if req_end < req_start: # slot also crosses midnight
                    req_end += 24.0
                elif req_end < open_time and req_start < open_time:
                    # Request is in early morning, shift to match
                    req_start += 24.0
                    req_end += 24.0
            
            # For a place to be valid, it should be open for at least half of the slot duration
            # Or at least intersect with it meaningfully
            slot_duration = req_end - req_start
            
            overlap_start = max(open_time, req_start)
            overlap_end = min(close_time, req_end)
            overlap_duration = overlap_end - overlap_start
            
            return overlap_duration >= (slot_duration * 0.4) # Must overlap at least 40% of the slot
    except Exception:
        pass
        
    return True # Safe fallback


@dataclass
class TimeSlotResult:
    time_range: str
    place_name: str
    category: str
    environment: str
    score: float
    note: str
    lat: float = 0.0
    lng: float = 0.0
    estimated_cost: float = 0.0
    google_maps_url: str = ""
    facebook_url: str = ""
    tripadvisor_url: str = ""
    image_url: str = ""


@dataclass
class DayPlanResult:
    day_number: int
    slots: list[TimeSlotResult] = field(default_factory=list)
    day_cost: float = 0.0


@dataclass
class ItineraryResult:
    days: list[DayPlanResult] = field(default_factory=list)
    total_estimated_cost: float = 0.0
    # Quãng đường chim bay nối các điểm theo thứ tự slot (km) — proxy cho tối ưu tuyến
    estimated_route_km: float = 0.0


# Default time slots for a day
DEFAULT_SLOTS = [
    {"name": "Điểm tâm", "start": "07:00", "end": "08:30", "type": "breakfast"},
    {"name": "Tham quan sáng", "start": "09:00", "end": "11:30", "type": "sightseeing"},
    {"name": "Ăn trưa", "start": "12:00", "end": "13:30", "type": "lunch"},
    {"name": "Tham quan chiều", "start": "14:00", "end": "16:30", "type": "sightseeing"},
    {"name": "Ăn vặt / Cafe", "start": "16:30", "end": "17:30", "type": "snack"},
    {"name": "Ăn tối", "start": "18:00", "end": "19:30", "type": "dinner"},
    {"name": "Hoạt động tối", "start": "20:00", "end": "22:00", "type": "nightlife"},
]


class ItineraryLogic:
    """
    Adaptive Itinerary Generator.
    Creates optimized multi-day travel plans with:
    - Time slot allocation based on operating hours
    - Culinary flow (correct meal ordering)
    - Weather awareness
    - Budget tracking
    - Anti-loop / destination fatigue prevention
    """

    def __init__(self, neo4j_client: Neo4jClient, milvus_client: MilvusClient, scoring_engine: ScoringEngine, trend_agent=None, redis_client=None, agent_team=None):
        self.neo4j = neo4j_client
        self.milvus = milvus_client
        self.scoring = scoring_engine
        self.trend_agent = trend_agent
        self.redis = redis_client
        self.agent_team = agent_team  # Multi-agent pipeline (Research + Planner)

    def _get_destination_center(self, destination: str) -> tuple:
        """Return approximate (lat, lng) center for known destinations."""
        d = destination.lower()
        if "đà nẵng" in d or "da nang" in d:
            return (16.0471, 108.2062)
        if "huế" in d or "hue" in d:
            return (16.4637, 107.5909)
        if "hội an" in d or "hoi an" in d:
            return (15.8801, 108.3380)
        return (0.0, 0.0)

    def _milvus_city_keywords(self, destination: str) -> list[str]:
        """Substrings used in Milvus `city like` filter (district/city field)."""
        kws = []
        d = destination.lower()
        if "đà nẵng" in destination or "da nang" in d:
            kws.append("Nẵng")
        if "huế" in destination or "hue" in d:
            kws.append("Huế")
        if "hội an" in destination or "hoi an" in d:
            kws.append("Hội An")
        return kws

    def _neo4j_city_labels(self, destination: str) -> list[str]:
        """City names for Neo4j CONTAINS / node matching."""
        labels = []
        d = destination.lower()
        if "đà nẵng" in destination or "da nang" in d:
            labels.append("Đà Nẵng")
        if "huế" in destination or "hue" in d:
            labels.append("Huế")
        if "hội an" in destination or "hoi an" in d:
            labels.append("Hội An")
        return labels

    def generate(self, user_id: str, destination: str, num_days: int,
                 start_date: str, preferences: list, budget_level: str = "medium",
                 transport_mode: str = "motorbike",
                 weather_forecast: str = "sunny") -> ItineraryResult:
        """
        Generate a complete multi-day itinerary.
        Primary: AgentTeam pipeline (Gemini Research + Planner).
        Fallback: Rule-based pipeline (Neo4j + Milvus + ScoringEngine).
        """
        logger.info(f"🗺️ Generating {num_days}-day itinerary for {destination}")

        wf = (weather_forecast or "sunny").strip().lower()
        if wf not in ("sunny", "cloudy", "rainy", "stormy"):
            wf = "sunny"

        # ── AgentTeam pipeline (primary) ──────────────────────────────────────
        if self.agent_team and self.agent_team.available:
            try:
                logger.info("🤖 Using AgentTeam pipeline (ResearchAgent + PlannerAgent)")
                result = self.agent_team.generate(
                    destination=destination,
                    num_days=num_days,
                    preferences=preferences,
                    weather_forecast=wf,
                    budget_level=budget_level,
                    transport_mode=transport_mode,
                )
                logger.info("✅ AgentTeam pipeline succeeded")
                return result
            except Exception as e:
                logger.warning(f"⚠️ AgentTeam failed ({e}) — falling back to rule-based pipeline")

        # ── Rule-based fallback pipeline ──────────────────────────────────────
        logger.info("📐 Using rule-based pipeline (Neo4j + Milvus + ScoringEngine)")

        # Per-request random seed ensures _pick_from_scored varies across calls
        request_seed = random.randint(0, 2**31)

        # Build preference query for vector search
        pref_text = f"Du lịch {destination} {' '.join(preferences)} {budget_level}"
        query_embedding = embed_text(pref_text)

        city_kws = self._milvus_city_keywords(destination)
        candidates = []
        seen_m: set[str] = set()
        if not city_kws:
            candidates = self.milvus.search_locations(
                query_embedding=query_embedding,
                city_filter=None,
                limit=100,
            )
        else:
            per = max(40, 100 // len(city_kws))
            for kw in city_kws:
                batch = self.milvus.search_locations(
                    query_embedding=query_embedding,
                    city_filter=kw,
                    limit=per,
                )
                for b in batch:
                    n = b.get("name")
                    if n and n not in seen_m:
                        seen_m.add(n)
                        candidates.append(b)

        city_labels = self._neo4j_city_labels(destination)
        graph_candidates = []
        if city_labels:
            seen_g: set[str] = set()
            for city in city_labels:
                for row in self.neo4j.find_nearby_locations(city, limit=28):
                    n = row.get("name")
                    if n and n not in seen_g:
                        seen_g.add(n)
                        graph_candidates.append(row)
        else:
            graph_candidates = self.neo4j.find_nearby_locations(destination, limit=30)

        # Merge and deduplicate candidates
        all_candidates = self._merge_candidates(candidates, graph_candidates)
        dest_center = self._get_destination_center(destination)
        
        # Determine intent early to adjust context
        pref_str = " ".join(preferences).lower()
        intent = "general"
        if any(t in pref_str for t in ["hot trend", "check-in", "hot", "mới"]):
            intent = "trend"
            logger.info(f"🔥 User intent classified as TREND for destination: {destination}. Will boost trendy places.")
            
            # --- TÍCH HỢP TREND ĐỘNG (KHÔNG HARDCODE) ---
            # Nếu user yêu cầu "trend", ta gọi TrendAgent để đi search Google Live.
            # TrendAgent ĐÃ ĐƯỢC SỬA PROMPT để BẮT BUỘC lấy tọa độ (lat/lng) chính xác từ bài review/bản đồ.
            try:
                if self.trend_agent:
                    search_query = f"Review các địa điểm du lịch, quán cafe check-in, quán ăn ngon hot nhất tại {destination} hiện nay có địa chỉ cụ thể"
                    trend_result = self.trend_agent.search(query=search_query, location=destination)
                    
                    live_candidates = []
                    for p in trend_result.results:
                        # NỚI LỎNG TOẠ ĐỘ: Nếu AI không tìm được lat/lng hợp lệ, lấy toạ độ trung tâm của một vài địa điểm trong list làm mỏ neo, hoặc tạm gán 0.0 nhưng không vứt bỏ.
                        # (Frontend map có thể không hiện ghim, nhưng text vẫn hiện trong lịch trình)
                        lat = getattr(p, 'lat', 0.0)
                        lng = getattr(p, 'lng', 0.0)
                        
                        live_candidates.append({
                            "name": p.name,
                            "category": p.category or "food",
                            "environment": "Outdoor",  # Mặc định an toàn cho trend
                            "description": p.description,
                            "trend_score": getattr(p, 'score', 0.9) * 10.0, # Scale up cho engine
                            "source_url": p.source_url,
                            "lat": float(lat) if lat else 0.0,
                            "lng": float(lng) if lng else 0.0,
                            "google_maps_url": p.source_url 
                        })
                        
                        if not lat or not lng:
                            logger.info(f"⚠️ Chấp nhận '{p.name}' dù không có toạ độ (Hot Trend).")
                    
                    if live_candidates:
                        # Chèn các địa điểm Trend có tọa độ THẬT vào chung với DB
                        all_candidates = live_candidates + all_candidates
                        logger.info(f"✨ Đã chèn thành công {len(live_candidates)} địa điểm Trend CÓ TỌA ĐỘ VÀO lịch trình!")
            except Exception as e:
                logger.error(f"Lỗi khi lấy Trend Động: {e}")

        # Generate day-by-day plan
        result = ItineraryResult()
        all_visited_categories = []  # Accumulate across days for cross-day anti-loop
        global_used_names = set() # PREVENT LOCATIONS FROM REPEATING ACROSS DAYS
        total_cost = 0.0

        for day in range(1, num_days + 1):
            # Check user intent from preferences
            intent = "general"
            pref_str = " ".join(preferences).lower()
            if any(t in pref_str for t in ["hot trend", "check-in", "hot", "mới"]):
                intent = "trend"
                
            ctx = ScoringContext(
                weather_condition=wf,
                time_of_day="morning",
                transport_mode=transport_mode,
                today_visited_categories=all_visited_categories.copy(),
                user_intent=intent,
            )

            # Build a dictionary of persona similarities (from Milvus vector search)
            # This is where the magic happens: Milvus already computed the cosine similarity based on preferences!
            persona_sims = {c.get("name"): c.get("score", 0.5) for c in all_candidates if "name" in c}
            
            day_plan = self._plan_day(day, all_candidates, ctx, budget_level, global_used_names, persona_sims, request_seed, dest_center)
            result.days.append(day_plan)
            total_cost += day_plan.day_cost

            # Track visited categories for cross-day anti-loop
            for slot in day_plan.slots:
                all_visited_categories.append(slot.category)
                global_used_names.add(slot.place_name)

        result.total_estimated_cost = total_cost
        result.estimated_route_km = self._estimate_route_km(result)
        logger.info(
            f"✅ Itinerary generated: {num_days} days, est. cost: {total_cost:,.0f} VNĐ, "
            f"route ~{result.estimated_route_km} km"
        )

        return result

    def _estimate_route_km(self, result: ItineraryResult) -> float:
        """Tổng khoảng cách chim bay giữa các điểm liên tiếp trong lịch (theo tài liệu GIS)."""
        total = 0.0
        for day_plan in result.days:
            prev: Optional[Tuple[float, float]] = None
            for s in day_plan.slots:
                lat, lng = float(s.lat or 0), float(s.lng or 0)
                if lat == 0.0 and lng == 0.0:
                    continue
                if prev is not None:
                    total += haversine_distance(prev[0], prev[1], lat, lng)
                prev = (lat, lng)
        return round(total, 2)

    def _pick_from_scored(
        self,
        scored: list,
        day_number: int,
        slot_type: str,
        request_seed: int = 0,
    ):
        """Weighted pick among top candidates so multi-day trips feel less repetitive."""
        if not scored:
            return None
        k = min(4, len(scored))
        top = scored[:k]
        if len(top) == 1:
            return top[0]
        # Include request_seed so different generate() calls produce different picks
        # even when the same top candidates appear.
        seed = int(
            hashlib.sha256(f"{day_number}:{slot_type}:{request_seed}".encode()).hexdigest(), 16
        ) % (2**32)
        rng = random.Random(seed)
        weights = [max(0.08, float(t.final_score)) for t in top]
        return rng.choices(top, weights=weights, k=1)[0]

    def reroute(self, itinerary_id: str, new_weather: str,
                current_lat: str, current_lng: str) -> ItineraryResult:
        """
        Re-route an existing itinerary when weather changes.
        Replaces outdoor activities with indoor alternatives.
        """
        logger.info(f"⛈️ Re-routing itinerary {itinerary_id} due to {new_weather}")

        # Search for indoor/sheltered alternatives near current location
        query_text = "Địa điểm trong nhà, có mái che, bảo tàng, cafe"
        query_embedding = embed_text(query_text)

        indoor_alternatives = self.milvus.search_locations(
            query_embedding=query_embedding,
            environment_filter="Indoor",
            limit=10,
        )

        # Also search for sheltered options
        sheltered_alternatives = self.milvus.search_locations(
            query_embedding=query_embedding,
            environment_filter="Sheltered",
            limit=5,
        )

        all_alternatives = indoor_alternatives + sheltered_alternatives

        # Build a single day re-routed plan
        ctx = ScoringContext(
            weather_condition=new_weather,
            transport_mode="car",  # Default to safest
        )

        day_plan = self._plan_day(1, all_alternatives, ctx, "medium")
        result = ItineraryResult(days=[day_plan], total_estimated_cost=day_plan.day_cost)
        result.estimated_route_km = self._estimate_route_km(result)

        logger.info(f"✅ Re-route complete: {len(day_plan.slots)} indoor activities planned")
        return result

    def swap_location(self, user_id: str, itinerary_id: str, place_name: str, category: str, lat: float, lng: float, destination: str) -> TimeSlotResult:
        """
        Swap a specific location with a similar one using vector search.
        """
        logger.info(f"🔄 Swapping {place_name} ({category}) near {lat},{lng} at {destination}")

        # Search for alternatives using Milvus
        query_text = f"Địa điểm {category} tương tự {place_name} ở {destination}"
        query_embedding = embed_text(query_text)

        city_keyword = ""
        if "Đà Nẵng" in destination or "Da Nang" in destination: city_keyword = "Nẵng"
        elif "Huế" in destination or "Hue" in destination: city_keyword = "Huế"
        elif "Hội An" in destination or "Hoi An" in destination: city_keyword = "Hội An"

        candidates = self.milvus.search_locations(
            query_embedding=query_embedding,
            city_filter=city_keyword,
            limit=20,
        )

        # Filter out the old place
        valid_candidates = [c for c in candidates if c.get("name") != place_name]

        if not valid_candidates:
            raise ValueError("Không tìm thấy địa điểm thay thế phù hợp")

        # Fetch metadata from Neo4j to enrich Milvus candidates (which lack lat/lng/URLs)
        names = [c.get("name") for c in valid_candidates if c.get("name")]
        neo4j_data = {}
        if names:
            try:
                query = """
                MATCH (l:Location)
                WHERE l.name IN $names
                RETURN l.name AS name, l.lat AS lat, l.lng AS lng, 
                       l.category AS category, l.environment AS environment, 
                       l.estimated_cost AS estimated_cost, l.google_maps_url AS google_maps_url,
                       l.facebook_url AS facebook_url, l.tripadvisor_url AS tripadvisor_url,
                       l.image_url AS image_url
                """
                results = self.neo4j.query(query, {"names": names})
                for r in results:
                    neo4j_data[r.get("name")] = r
            except Exception as e:
                logger.error(f"Failed to fetch Neo4j metadata for swap_location: {e}")

        enriched_candidates = []
        for c in valid_candidates:
            name = c.get("name")
            if name in neo4j_data:
                db_item = neo4j_data[name]
                enriched = db_item.copy()
                enriched["similarity"] = c.get("similarity", 0.5)
                
                try: enriched["lat"] = float(enriched["lat"])
                except: enriched["lat"] = 0.0
                try: enriched["lng"] = float(enriched["lng"])
                except: enriched["lng"] = 0.0
                
                enriched_candidates.append(enriched)
            else:
                c["similarity"] = c.get("similarity", 0.5)
                enriched_candidates.append(c)

        valid_candidates = enriched_candidates

        # Pick the best one based on distance + score if lat/lng are provided
        best = valid_candidates[0]
        if lat and lng and lat != 0.0 and lng != 0.0:
            def sort_key(c):
                c_lat = float(c.get("lat") or 0.0)
                c_lng = float(c.get("lng") or 0.0)
                if c_lat == 0.0 or c_lng == 0.0:
                    return 999.0 # penalize missing coords
                dist = haversine_distance(lat, lng, c_lat, c_lng)
                return dist - c.get("similarity", 0)*5  # Balance distance and relevance score

            valid_candidates.sort(key=sort_key)
            best = valid_candidates[0]
            
        est_cost = best.get("estimated_cost", {})
        cost = est_cost.get("max", 0) if isinstance(est_cost, dict) else (est_cost if isinstance(est_cost, (int, float)) else 0)

        return TimeSlotResult(
            time_range="", # Left empty so the backend can inherit from the old slot
            place_name=best.get("name", "Unknown"),
            category=best.get("category", category),
            environment=best.get("environment", "Outdoor"),
            score=round(best.get("similarity", 0.5), 3),
            note=f"Đề xuất thay thế cho {place_name}",
            lat=float(best.get("lat") or 0.0),
            lng=float(best.get("lng") or 0.0),
            estimated_cost=float(cost),
            google_maps_url=best.get("google_maps_url", ""),
            facebook_url=best.get("facebook_url", ""),
            tripadvisor_url=best.get("tripadvisor_url", ""),
            image_url=best.get("image_url", "")
        )

    def _plan_day(self, day_number: int, candidates: list,
                  ctx: ScoringContext, budget_level: str, global_used_names: set | None = None,
                  persona_similarities: dict | None = None, request_seed: int = 0,
                  dest_center: tuple = (0.0, 0.0)) -> DayPlanResult:
        """Plan activities for a single day using time slot allocation."""
        if global_used_names is None:
            global_used_names = set()

        day = DayPlanResult(day_number=day_number)
        used_names = set(global_used_names)
        day_cost = 0.0

        # Seed the distance context with the destination center so that even the very
        # first slot of the day benefits from geo-filtering (without this, prev_lat=0,0
        # disables the distance penalty for the opening slot).
        ctx.prev_lat = dest_center[0]
        ctx.prev_lng = dest_center[1]

        for slot_template in DEFAULT_SLOTS:
            slot_type = slot_template["type"]
            slot_start = slot_template["start"]
            slot_end = slot_template["end"]

            # Lọc theo giờ hoạt động (Operating Hours) TRƯỚC TIÊN
            # Giữ lại những ứng viên đang mở cửa hoặc không có thông tin đóng cửa
            open_candidates = [
                c for c in candidates 
                if is_open_during_slot(c.get("operating_hours", ""), slot_start, slot_end)
            ]
            
            # Nếu bộ lọc giờ quá gắt (còn ít quá), fallback lại toàn bộ
            if len(open_candidates) < 5:
                open_candidates = candidates

            # Filter candidates by slot type
            # Fallback strategy: if strict filtering yields nothing, slowly loosen criteria
            slot_candidates = []
            
            if slot_type in ("breakfast", "lunch", "dinner", "snack"):
                # Strict: Food slot matching meal time
                slot_candidates = [
                    c for c in open_candidates
                    if c.get("category", "") in ("food", "restaurant")
                    and slot_type in c.get("meal_time", [slot_type])
                    and c.get("name") not in used_names
                ]
                # Less strict: Any food place, BUT prevent nightlife/bars for breakfast and lunch
                if len(slot_candidates) < 5:
                    if slot_type in ("breakfast", "lunch"):
                        slot_candidates = [
                            c for c in open_candidates
                            if c.get("category", "") in ("food", "restaurant")
                            and c.get("category", "") != "nightlife"
                            and not any(k in c.get("name", "").lower() for k in ["bar", "pub", "club", "lounge"])
                            and c.get("name") not in used_names
                        ]
                    else:
                        slot_candidates = [
                            c for c in open_candidates
                            if c.get("category", "") in ("food", "restaurant")
                            and c.get("name") not in used_names
                        ]
            else:
                # Sightseeing/nightlife: strictly non-food
                slot_candidates = [
                    c for c in open_candidates
                    if c.get("category", "") not in ("food", "restaurant")
                    and c.get("name") not in used_names
                ]

            # Last resort fallback: Any unused location, BUT still block bars in the morning
            if len(slot_candidates) == 0:
                hour_start = int(slot_template["start"].split(":")[0])
                if hour_start < 17:
                    slot_candidates = [
                        c for c in open_candidates 
                        if c.get("name") not in used_names
                        and c.get("category", "") != "nightlife"
                        and not any(k in c.get("name", "").lower() for k in ["bar", "pub", "club", "lounge"])
                    ]
                else:
                    slot_candidates = [c for c in open_candidates if c.get("name") not in used_names]

            if not slot_candidates:
                continue

                # Set time context for scoring
            hour = int(slot_template["start"].split(":")[0])
            
            # Boost trend candidates if user wants trend
            if ctx.user_intent == "trend":
                for c in slot_candidates:
                    if c.get("tiktok_url") or c.get("facebook_url"):
                        c["trend_score"] = 10.0
                        cat = c.get("category")
                        c["category"] = (str(cat) if cat is not None else "food") + " (Hot Trend)"
            if hour < 12:
                ctx.time_of_day = "morning"
            elif hour < 17:
                ctx.time_of_day = "afternoon"
            elif hour < 20:
                ctx.time_of_day = "evening"
            else:
                ctx.time_of_day = "night"
            ctx.current_hour = hour
            
            # Update distance context from the last slot that had real coordinates.
            # Fall back to dest_center (never 0,0) so the penalty always fires.
            if len(day.slots) > 0:
                last = day.slots[-1]
                if last.lat and last.lng:
                    ctx.prev_lat = last.lat
                    ctx.prev_lng = last.lng
                # else: keep whatever was set previously (dest_center or last valid slot)

            # Score and pick best candidate
            scored = self.scoring.score_and_rank(slot_candidates, ctx, persona_similarities or {})

            if not scored:
                continue

            best = self._pick_from_scored(scored, day_number, slot_type, request_seed)
            if best is None:
                continue
            best_data = next((c for c in slot_candidates if c.get("name") == best.name), {})

            est_cost = best_data.get("estimated_cost", {})
            cost = est_cost.get("max", 0) if isinstance(est_cost, dict) else 0

            logger.info(
                f"Map debug for {best.name}: lat={best_data.get('lat')}, lng={best_data.get('lng')}"
            )
            
            slot = TimeSlotResult(
                time_range=f"{slot_template['start']}-{slot_template['end']}",
                place_name=best.name,
                category=best_data.get("category", slot_type),
                environment=best_data.get("environment", "Unknown"),
                score=round(best.final_score / 1.5, 3), # Normalize roughly back to 0-1 for the UI (max score is around 1.5)
                note=self._generate_note(best_data, ctx),
                lat=float(best_data.get("lat", 0.0) or 0.0),
                lng=float(best_data.get("lng", 0.0) or 0.0),
                estimated_cost=cost,
                google_maps_url=best_data.get("google_maps_url", ""),
                facebook_url=best_data.get("facebook_url", ""),
                tripadvisor_url=best_data.get("tripadvisor_url", ""),
                image_url=best_data.get("image_url", "")
            )

            day.slots.append(slot)
            day_cost += cost
            used_names.add(best.name)

        day.day_cost = day_cost
        return day

    def _merge_candidates(self, vector_results: list, graph_results: list) -> list:
        """Merge and deduplicate candidates from Milvus and Neo4j, combining their attributes."""
        seen = {}

        # First, index Graph results because they have the most accurate lat/lng from Neo4j
        for r in graph_results:
            name = r.get("name", "")
            if name:
                # Ensure lat/lng are properly parsed as floats
                if "lat" in r and r["lat"]:
                    try: r["lat"] = float(r["lat"])
                    except: r["lat"] = 0.0
                if "lng" in r and r["lng"]:
                    try: r["lng"] = float(r["lng"])
                    except: r["lng"] = 0.0
                seen[name] = r

        # Then merge Vector results
        for r in vector_results:
            name = r.get("name", "")
            if name:
                if name in seen:
                    # If we already have it from Neo4j, just keep Neo4j's superior metadata 
                    # but maybe update the score if needed
                    pass
                else:
                    if "lat" in r and r["lat"]:
                        try: r["lat"] = float(r["lat"])
                        except: r["lat"] = 0.0
                    if "lng" in r and r["lng"]:
                        try: r["lng"] = float(r["lng"])
                        except: r["lng"] = 0.0
                    seen[name] = r

        # As a fallback, if any candidate somehow doesn't have lat/lng, attempt to fetch directly
        neo4j = self.neo4j
            
        final_list = list(seen.values())
        
        # Dynamic distance filter to prevent crazy jumps (e.g. going to the border or another province)
        valid_coords = []
        for r in final_list:
            lat = r.get("lat", 0.0)
            lng = r.get("lng", 0.0)
            if lat and lng and float(lat) != 0.0 and float(lng) != 0.0:
                valid_coords.append((float(lat), float(lng)))

        filtered_list = final_list
        if valid_coords:
            # Find approximate center (median) to represent the destination's main cluster
            lats = sorted([c[0] for c in valid_coords])
            lngs = sorted([c[1] for c in valid_coords])
            med_lat = lats[len(lats)//2]
            med_lng = lngs[len(lngs)//2]
            
            filtered_list = []
            for r in final_list:
                lat = float(r.get("lat", 0.0) or 0.0)
                lng = float(r.get("lng", 0.0) or 0.0)
                
                if lat == 0.0 or lng == 0.0:
                    filtered_list.append(r) # Keep it to be resolved below
                    continue
                
                # Calculate distance from the central cluster
                dist = haversine_distance(med_lat, med_lng, lat, lng)
                
                # 45km radius allows Ba Na Hills (~24km) and Hoi An (~24km) from Da Nang
                # but cuts out extreme outliers in the mountains (50km+)
                if dist <= 45.0:
                    filtered_list.append(r)
                else:
                    logger.info(f"🚫 Dropped '{r.get('name')}' because it is {dist:.1f}km away from median center (Too far!)")
                    
        for r in filtered_list:
            if not r.get("lat") or not r.get("lng") or r.get("lat") == 0.0:
                # Try a direct query to get lat/lng if missing (search both Location and FoodPlace)
                try:
                    res = neo4j.query(
                        "MATCH (l) WHERE (l:Location OR l:FoodPlace) AND l.name = $name "
                        "RETURN l.lat AS lat, l.lng AS lng, l.meal_time AS meal_time, "
                        "l.estimated_cost AS estimated_cost, l.operating_hours AS operating_hours LIMIT 1",
                        {"name": r.get("name", "")}
                    )
                    if res and len(res) > 0:
                        row = res[0]
                        if row.get("lat") and row.get("lng"):
                            r["lat"] = float(row["lat"])
                            r["lng"] = float(row["lng"])
                        # Also backfill other missing fields critical for scoring
                        if not r.get("meal_time") and row.get("meal_time"):
                            r["meal_time"] = row["meal_time"]
                        if not r.get("estimated_cost") and row.get("estimated_cost"):
                            r["estimated_cost"] = row["estimated_cost"]
                        if not r.get("operating_hours") and row.get("operating_hours"):
                            r["operating_hours"] = row["operating_hours"]
                except Exception:
                    pass

        return filtered_list

    def _generate_note(self, location: dict, ctx: ScoringContext) -> str:
        """Generate contextual note for a time slot."""
        notes = []

        if ctx.weather_condition in ("rainy", "stormy"):
            if location.get("environment", "").lower() == "indoor":
                notes.append("✅ Trong nhà, an toàn khi mưa")
            else:
                notes.append("⚠️ Mang ô/áo mưa")

        if location.get("car_parking") is False and ctx.transport_mode == "car":
            notes.append("🅿️ Không có chỗ đậu ô tô, nên đi xe máy/đi bộ")

        if location.get("cultural_note"):
            notes.append(f"📝 {location['cultural_note']}")

        return " | ".join(notes) if notes else ""
