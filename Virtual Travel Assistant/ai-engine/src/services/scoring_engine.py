"""
LUNA AI Engine - Adaptive Scoring Engine (Rule Engine)
Calculates FinalScore for each location based on multiple weighted factors.
Formula: FinalScore = w1·S_persona + w2·S_weather + w3·S_trend + w4·S_culinary + w5·S_logistics - P_penalties
"""

import math
import logging
from dataclasses import dataclass, field
from datetime import datetime

from src.config.settings import settings
from src.utils.nlp import is_spiritual_location
from src.utils.geo import haversine_distance

logger = logging.getLogger(__name__)

@dataclass
class ScoringContext:
    """Context for scoring a location."""
    weather_condition: str = "sunny"      # sunny/cloudy/rainy/stormy
    temperature: float = 28.0
    time_of_day: str = "morning"          # morning/afternoon/evening/night
    current_hour: int = 10
    transport_mode: str = "motorbike"     # walk/motorbike/car
    today_visited_categories: list = field(default_factory=list)
    user_intent: str = "general"          # general/trend/history
    prev_lat: float = 0.0                 # Previous location lat for distance scoring
    prev_lng: float = 0.0                 # Previous location lng for distance scoring


@dataclass
class LocationScore:
    """Scored location with breakdown."""
    name: str
    final_score: float
    s_persona: float = 0.0
    s_weather: float = 0.0
    s_trend: float = 0.0
    s_culinary: float = 0.0
    s_logistics: float = 0.0
    penalties: float = 0.0
    blocked: bool = False
    block_reason: str = ""


class ScoringEngine:
    """
    Adaptive Scoring Engine — Rule-based scoring for location recommendations.
    Uses dynamic weight adjustment based on context (weather, intent, transport).
    """

    def __init__(self):
        # Default weights (sunny weather, normal mode)
        self.default_weights = {
            "persona": settings.w_persona,   # 0.35
            "weather": settings.w_weather,   # 0.10
            "trend": settings.w_trend,       # 0.25
            "culinary": settings.w_culinary, # 0.15
            "logistics": settings.w_logistics, # 0.15
        }

    def get_dynamic_weights(self, ctx: ScoringContext) -> dict:
        """
        Adjust weights dynamically based on current context.
        See: SYSTEM_DESIGN.md Section 4.2 — Trọng số điều chỉnh động
        """
        weights = self.default_weights.copy()

        # === RAINY / STORMY → Weather weight increases ===
        if ctx.weather_condition in ("rainy", "stormy"):
            weights["persona"] = 0.20
            weights["weather"] = 0.40
            weights["trend"] = 0.10
            weights["culinary"] = 0.15
            weights["logistics"] = 0.15
            logger.info("⛈️ Weights adjusted for rainy/stormy weather")

        # === USER ASKS FOR TREND → Trend weight increases (but don't overpower persona) ===
        elif ctx.user_intent == "trend":
            weights["persona"] = 0.25
            weights["weather"] = 0.10
            weights["trend"] = 0.35
            weights["culinary"] = 0.15
            weights["logistics"] = 0.15
            logger.info("🔥 Weights adjusted for trend-focused query (balanced)")

        # === CAR TRANSPORT → Logistics weight increases ===
        elif ctx.transport_mode == "car":
            weights["persona"] = 0.30
            weights["weather"] = 0.10
            weights["trend"] = 0.20
            weights["culinary"] = 0.10
            weights["logistics"] = 0.30
            logger.info("🚗 Weights adjusted for car transport")

        return weights

    def score_location(self, location: dict, ctx: ScoringContext,
                        persona_similarity: float = 0.5) -> LocationScore:
        """
        Score a single location based on all factors.

        Args:
            location: Dict with location metadata (from Neo4j/Milvus)
            ctx: Current context (weather, time, transport)
            persona_similarity: Cosine similarity with user persona vector (0-1)
        """
        result = LocationScore(name=location.get("name", "Unknown"), final_score=0.0)

        # === HARD BLOCK CHECKS (before scoring) ===
        block_reason = self._check_hard_blocks(location, ctx)
        if block_reason:
            result.blocked = True
            result.block_reason = block_reason
            result.final_score = -float("inf")
            return result

        weights = self.get_dynamic_weights(ctx)

        # === S_persona: Persona matching score ===
        result.s_persona = persona_similarity

        # === S_weather: Weather suitability score ===
        result.s_weather = self._calc_weather_score(location, ctx)

        # === S_trend: Trend score ===
        result.s_trend = self._calc_trend_score(location)

        # === S_culinary: Culinary flow score ===
        result.s_culinary = self._calc_culinary_score(location, ctx)

        # === S_logistics: Logistics score ===
        result.s_logistics = self._calc_logistics_score(location, ctx)

        # === P_penalties: Penalty deductions ===
        result.penalties = self._calc_penalties(location, ctx)

        # === Final Score ===
        try:
            result.final_score = float(
                weights["persona"] * float(result.s_persona)
                + weights["weather"] * float(result.s_weather)
                + weights["trend"] * float(result.s_trend)
                + weights["culinary"] * float(result.s_culinary)
                + weights["logistics"] * float(result.s_logistics)
                - float(result.penalties)
            )
        except Exception as e:
            logger.error(f"Error calculating final score for {result.name}: {e}")
            result.final_score = 0.0

        return result

    def score_and_rank(self, locations: list, ctx: ScoringContext,
                        persona_similarities: dict | None = None) -> list[LocationScore]:
        """Score multiple locations and return ranked results (highest first)."""
        persona_similarities = persona_similarities or {}

        scored = []
        for loc in locations:
            name = loc.get("name", "")
            sim = persona_similarities.get(name, 0.5)
            result = self.score_location(loc, ctx, sim)
            if not result.blocked:
                scored.append(result)

        # Sort by final_score descending
        scored.sort(key=lambda x: x.final_score, reverse=True)

        # Drop heavily-penalized (very far) candidates only when better alternatives exist.
        # This prevents picking a location 100km away when there are close ones available.
        if any(r.final_score >= 0 for r in scored):
            scored = [r for r in scored if r.final_score >= -0.5]

        return scored

    def _check_hard_blocks(self, location: dict, ctx: ScoringContext) -> str:
        """
        Hard-block checks. Returns reason string if blocked, empty string if OK.
        """
        name = location.get("name", "")
        category = location.get("category", "").lower()

        # === TABOO FILTER: Nightlife in the morning ===
        is_bar_pub = any(keyword in name.lower() for keyword in ["bar", "pub", "club", "lounge", "tavern"]) or category == "nightlife"
        if is_bar_pub and ctx.current_hour < 17:
            return f"Thời gian không hợp lý: Không đi Bar/Pub ({name}) vào ban ngày."

        # === TABOO FILTER: Spiritual locations at night ===
        if is_spiritual_location(name) and ctx.current_hour >= 20:
            return f"An toàn tâm linh: {name} không phù hợp vào ban đêm"

        # === EXTREME WEATHER: Block outdoor in storm ===
        if ctx.weather_condition == "stormy":
            env = location.get("environment", "outdoor")
            if env.lower() == "outdoor":
                return f"Thời tiết nguy hiểm: {name} ngoài trời không an toàn khi bão"

        # === SEASONAL DANGER ===
        danger_months = location.get("danger_season", [])
        current_month = datetime.now().month
        if danger_months and current_month in danger_months:
            closure_reason = location.get("seasonal_closure", "Mùa nguy hiểm")
            return f"Chặn theo mùa: {name} - {closure_reason}"

        # === EXTREME WEATHER BLOCK ===
        if location.get("extreme_weather_block") and ctx.weather_condition in ("rainy", "stormy"):
            return f"Chặn thời tiết cực đoan: {name} không an toàn"

        return ""

    def _calc_weather_score(self, location: dict, ctx: ScoringContext) -> float:
        """Calculate weather suitability score (0-1)."""
        env = location.get("environment", "outdoor").lower()
        weather = ctx.weather_condition

        if weather in ("rainy", "stormy"):
            if env == "indoor":
                return 1.0
            elif env == "sheltered":
                return 0.7
            else:
                return 0.1  # outdoor in rain = bad
        elif weather == "sunny":
            if env == "outdoor":
                return 1.0  # outdoor in sun = great
            elif env == "sheltered":
                return 0.9
            else:
                return 0.7  # indoor on a sunny day = less ideal
        return 0.5  # cloudy = neutral

    def _calc_trend_score(self, location: dict) -> float:
        """Calculate normalized trend score (0-1). Boost if has good rating or social links."""
        raw = location.get("trend_score", 0)
        try: raw = float(raw) if raw is not None else 0.0
        except: raw = 0.0
        
        # If data is from crawler, use estimated_rating as a trend proxy
        rating = location.get("estimated_rating", 0)
        try: rating = float(rating) if rating is not None else 0.0
        except: rating = 0.0
        
        if rating > 0:
            # Map a 5-star rating to a 0-10 score (e.g. 4.5 star -> 9)
            raw = max(raw, rating * 2)
            
        # Social media boost (tiktok, facebook) implies it's "trendy"
        if location.get("tiktok_url") or location.get("facebook_url"):
            raw += 2.0
            
        # Normalize from 0-10 scale to 0-1
        return min(raw / 10.0, 1.0)

    def _calc_culinary_score(self, location: dict, ctx: ScoringContext) -> float:
        """Culinary Flow — is this the right food for this time of day?"""
        meal_time = location.get("meal_time", [])
        if not meal_time:
            return 0.5  # Not a food place, neutral

        time_mapping = {
            "morning": "breakfast",
            "afternoon": "lunch",
            "evening": "dinner",
            "night": "dinner",
        }
        expected_meal = time_mapping.get(ctx.time_of_day, "lunch")

        if expected_meal in meal_time:
            return 1.0  # Perfect match
        return 0.0  # Wrong time for this food

    def _calc_logistics_score(self, location: dict, ctx: ScoringContext) -> float:
        """Logistics score based on transport mode and accessibility."""
        score = 0.5  # base

        if ctx.transport_mode == "car":
            # Car needs parking
            if location.get("car_parking", False):
                score += 0.3
            else:
                score -= 0.3
            # Car can't access alleys
            if location.get("alley_access", False):
                score -= 0.2
        elif ctx.transport_mode == "motorbike":
            # Motorbike can go anywhere, slight bonus for parking
            if location.get("motorbike_parking", True):
                score += 0.2
        elif ctx.transport_mode == "walk":
            # Walking prefers nearby and easy access
            score += 0.3

        return max(0.0, min(1.0, score))

    def _calc_penalties(self, location: dict, ctx: ScoringContext) -> float:
        """Calculate penalty deductions."""
        penalties = 0.0

        # === Anti-Loop: Heavy penalty if category already visited today ===
        category = location.get("category", "")
        if category and category in ctx.today_visited_categories:
            penalties += 2.0  # Massive penalty to force variety

        # === Crowd Control: Reduce if density high ===
        try:
            current_density = float(location.get("current_density", 0))
            max_capacity = float(location.get("max_capacity", 100))
            if max_capacity > 0 and current_density > max_capacity * 0.8:
                penalties += 0.3
        except:
            pass

        # === Ingredient Season Check ===
        ingredient_season = location.get("ingredient_season", None)
        if ingredient_season:
            current_month = datetime.now().month
            if current_month not in ingredient_season:
                penalties += 0.4  # Out of season ingredient

        # === Distance Penalty ===
        # Avoid zig-zagging. If previous location is known, penalize locations that are too far.
        if ctx.prev_lat and ctx.prev_lng:
            loc_lat = float(location.get("lat", 0.0) or 0.0)
            loc_lng = float(location.get("lng", 0.0) or 0.0)
            if loc_lat and loc_lng:
                dist = haversine_distance(ctx.prev_lat, ctx.prev_lng, loc_lat, loc_lng)
                if dist > 30.0:
                    penalties += 5.0 # Very heavy penalty to drop it entirely (>30km jump for a single slot)
                elif dist > 15.0:
                    penalties += 2.0 # Heavy penalty (>15km jump)
                elif dist > 8.0:
                    penalties += 1.0 # Medium penalty
                elif dist > 3.0:
                    penalties += 0.3 # Small penalty
                else:
                    penalties -= 0.5 # BONUS for being close (< 3km)!

        return penalties
