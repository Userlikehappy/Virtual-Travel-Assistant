"""
LUNA AI Engine - gRPC Server (Thin Layer)
Only handles request routing and response formatting.
All business logic is in the services layer.
"""

import time
import logging
import grpc
from concurrent import futures
from dataclasses import asdict
import sys
import os

# Add grpc_server directory to path for protobuf imports
sys.path.insert(0, os.path.dirname(__file__))

import luna_service_pb2 as pb2
import luna_service_pb2_grpc as pb2_grpc

logger = logging.getLogger(__name__)


class LunaAIServicer(pb2_grpc.LunaAIServicer):
    """
    Thin gRPC Server — Only routing and response formatting.
    All logic delegated to injected services (Dependency Injection).
    """

    def __init__(self, chat_logic, itinerary_logic, trend_agent):
        self.chat_logic = chat_logic
        self.itinerary_logic = itinerary_logic
        self.trend_agent = trend_agent
        self._start_time = time.time()

    def Chat(self, request, context):
        """Handle chat request — route to appropriate service."""
        if not request.user_id or not request.message:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("user_id and message are required")
            return pb2.ChatResponse()

        try:
            ctx_info = None
            if request.context:
                ctx_info = {
                    "current_location": request.context.current_location or "",
                    "weather_condition": request.context.weather_condition or "",
                    "temperature": request.context.temperature or 0.0,
                    "transport_mode": request.context.transport_mode or "",
                    "time_of_day": request.context.time_of_day or "",
                    "persona_profile": getattr(
                        request.context, "persona_profile", ""
                    )
                    or "",
                }

            raw = self.chat_logic.process(
                user_id=request.user_id,
                message=request.message,
                session_id=request.session_id,
                context=ctx_info,
            )

            result = asdict(raw) if hasattr(raw, '__dataclass_fields__') else (raw if isinstance(raw, dict) else {"reply": str(raw), "source_type": "error", "sources": []})

            response = pb2.ChatResponse()
            response.reply = result.get("reply", "")
            response.source_type = result.get("source_type", "historian")
            # sources in ChatResponse is `repeated string sources = 3;`
            for s in result.get("sources", []):
                # If s is a dict, try to get string representation or title
                if isinstance(s, dict):
                    val = s.get("title", "") or str(s)
                    response.sources.append(val)
                else:
                    response.sources.append(str(s))
            return response

        except Exception as e:
            logger.error(f"Chat RPC error: {e}")
            # Return a fallback response instead of failing
            response = pb2.ChatResponse()
            response.reply = f"Xin lỗi, đã xảy ra lỗi: {str(e)}"
            response.source_type = "error"
            return response

    def GenerateItinerary(self, request, context):
        """Handle itinerary generation request."""
        if not request.user_id or not request.destination or request.num_days <= 0:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("user_id, destination, and num_days (>0) are required")
            return pb2.ItineraryResponse()

        try:
            raw = self.itinerary_logic.generate(
                user_id=request.user_id,
                destination=request.destination,
                num_days=request.num_days,
                start_date=request.start_date,
                preferences=list(request.preferences),
                budget_level=request.budget_level or 'medium',
                transport_mode=request.transport_mode or 'motorbike',
                weather_forecast=(request.weather_forecast or 'sunny').strip().lower(),
            )
            result = asdict(raw) if hasattr(raw, '__dataclass_fields__') else raw

            response = pb2.ItineraryResponse()
            response.total_estimated_cost = float(result.get("total_estimated_cost", 0))
            response.estimated_route_km = float(
                result.get("estimated_route_km", 0) or 0
            )

            for day_data in result.get("days", []):
                day = response.days.add()
                day.day_number = int(day_data.get("day_number", 0))
                day.day_cost = float(day_data.get("day_cost", 0))
                for slot_data in day_data.get("slots", []):
                    slot = day.slots.add()
                    slot.time_range = slot_data.get("time_range", "")
                    slot.place_name = slot_data.get("place_name", "")
                    slot.category = slot_data.get("category", "")
                    slot.environment = slot_data.get("environment", "Outdoor")
                    slot.score = float(slot_data.get("score", 0))
                    slot.note = slot_data.get("note", "")
                    slot.lat = float(slot_data.get("lat", 0) or 0)
                    slot.lng = float(slot_data.get("lng", 0) or 0)
                    slot.estimated_cost = float(slot_data.get("estimated_cost", 0))
                    slot.google_maps_url = str(slot_data.get("google_maps_url", ""))
                    slot.facebook_url = str(slot_data.get("facebook_url", ""))
                    slot.tripadvisor_url = str(slot_data.get("tripadvisor_url", ""))
                    slot.image_url = str(slot_data.get("image_url", ""))

            return response

        except Exception as e:
            logger.error(f"GenerateItinerary RPC error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return pb2.ItineraryResponse()

    def ReRouteItinerary(self, request, context):
        """Handle weather re-route request."""
        try:
            raw = self.itinerary_logic.reroute(
                itinerary_id=request.itinerary_id,
                new_weather=request.new_weather,
                current_lat=request.current_location_lat,
                current_lng=request.current_location_lng,
            )
            result = asdict(raw) if hasattr(raw, '__dataclass_fields__') else raw

            response = pb2.ItineraryResponse()
            response.total_estimated_cost = float(result.get("total_estimated_cost", 0))
            response.estimated_route_km = float(
                result.get("estimated_route_km", 0) or 0
            )

            for day_data in result.get("days", []):
                day = response.days.add()
                day.day_number = int(day_data.get("day_number", 0))
                day.day_cost = float(day_data.get("day_cost", 0))
                for slot_data in day_data.get("slots", []):
                    slot = day.slots.add()
                    slot.time_range = slot_data.get("time_range", "")
                    slot.place_name = slot_data.get("place_name", "")
                    slot.category = slot_data.get("category", "")
                    slot.environment = slot_data.get("environment", "Outdoor")
                    slot.score = float(slot_data.get("score", 0))
                    slot.note = slot_data.get("note", "")
                    slot.lat = float(slot_data.get("lat", 0) or 0)
                    slot.lng = float(slot_data.get("lng", 0) or 0)
                    slot.estimated_cost = float(slot_data.get("estimated_cost", 0))
                    slot.google_maps_url = str(slot_data.get("google_maps_url", ""))
                    slot.facebook_url = str(slot_data.get("facebook_url", ""))
                    slot.tripadvisor_url = str(slot_data.get("tripadvisor_url", ""))
                    slot.image_url = str(slot_data.get("image_url", ""))

            return response

        except Exception as e:
            logger.error(f"ReRouteItinerary RPC error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return pb2.ItineraryResponse()

    def SwapLocation(self, request, context):
        """Handle location swap request."""
        try:
            raw = self.itinerary_logic.swap_location(
                user_id=request.user_id,
                itinerary_id=request.itinerary_id,
                place_name=request.place_name,
                category=request.category,
                lat=request.lat,
                lng=request.lng,
                destination=request.destination
            )
            result = asdict(raw) if hasattr(raw, '__dataclass_fields__') else raw

            response = pb2.SwapResponse()
            slot = response.new_slot
            slot.time_range = str(result.get("time_range", ""))
            slot.place_name = str(result.get("place_name", ""))
            slot.category = str(result.get("category", ""))
            slot.environment = str(result.get("environment", ""))
            slot.score = float(result.get("score", 0.0))
            slot.note = str(result.get("note", ""))
            slot.lat = float(result.get("lat", 0.0))
            slot.lng = float(result.get("lng", 0.0))
            slot.estimated_cost = float(result.get("estimated_cost", 0.0))
            slot.google_maps_url = str(result.get("google_maps_url", ""))
            slot.facebook_url = str(result.get("facebook_url", ""))
            slot.tripadvisor_url = str(result.get("tripadvisor_url", ""))
            slot.image_url = str(result.get("image_url", ""))

            return response

        except Exception as e:
            logger.error(f"SwapLocation RPC error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return pb2.SwapResponse()

    def SearchTrend(self, request, context):
        """Handle synchronous trend search request via Google Search Grounding."""
        if not request.query or not request.location:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("query and location are required")
            return pb2.TrendResponse()

        try:
            raw = self.trend_agent.search(
                query=request.query,
                location=request.location,
            )
            result = asdict(raw) if hasattr(raw, '__dataclass_fields__') else (raw if isinstance(raw, dict) else raw.to_dict())

            response = pb2.TrendResponse()
            response.query = result.get("query", "")
            response.location = result.get("location", "")
            
            for item in result.get("results", []):
                p = response.results.add()
                p.name = str(item.get("name", ""))
                p.description = str(item.get("description", ""))
                p.source_url = str(item.get("source_url", ""))
                p.category = str(item.get("category", ""))
                p.location = str(item.get("location", ""))
                p.score = float(item.get("score", 0))
                p.lat = float(item.get("lat", 0))
                p.lng = float(item.get("lng", 0))

            return response
            
        except Exception as e:
            logger.error(f"SearchTrend RPC error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return pb2.TrendResponse()

    def HealthCheck(self, request, context):
        """Health check endpoint."""
        uptime = int(time.time() - self._start_time)
        response = pb2.HealthStatus()
        response.status = "healthy"
        response.uptime_seconds = uptime
        return response


def create_grpc_server(servicer, host: str, port: int, max_workers: int):
    """Create and configure the gRPC server."""
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=max_workers),
        options=[
            ("grpc.max_send_message_length", 50 * 1024 * 1024),  # 50MB
            ("grpc.max_receive_message_length", 50 * 1024 * 1024),
        ],
    )

    # Register servicer with generated protobuf stubs
    pb2_grpc.add_LunaAIServicer_to_server(servicer, server)

    server.add_insecure_port(f"{host}:{port}")
    return server
