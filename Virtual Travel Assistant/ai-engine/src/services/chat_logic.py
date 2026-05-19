"""
LUNA AI Engine - Chat Logic (Nhà Sử Học / Historian)
LightRAG Dual Retrieval: Vector Search (Milvus) + Graph Traversal (Neo4j) → LLM Synthesis
"""

import logging
from google import genai
from google.genai import types
from dataclasses import dataclass

from src.config.settings import settings
from src.clients.neo4j_client import Neo4jClient
from src.clients.milvus_client import MilvusClient
from src.clients.mongo_client import MongoDBClient
from src.utils.embedding import embed_text
from src.utils.nlp import classify_query_intent
from src.services.agent_skills import calculate_distance, check_opening_hours, get_current_weather

logger = logging.getLogger(__name__)

AVAILABLE_TOOLS = {
    "calculate_distance": calculate_distance,
    "check_opening_hours": check_opening_hours,
    "get_current_weather": get_current_weather
}

@dataclass
class ChatResult:
    reply: str
    source_type: str  # "historian" | "reporter" | "itinerary"
    sources: list[dict]  # [{title, url, confidence}]


class ChatLogic:
    """
    Core Chat Logic — LightRAG Dual Retrieval (Nhà Sử Học).
    Thin Server calls this service for all chat processing.
    """

    SYSTEM_PROMPT_HISTORIAN = """Bạn là LUNA — Trợ lý du lịch AI chuyên về miền Trung Việt Nam (Huế - Đà Nẵng - Hội An).
Vai trò: Nhà Sử Học bản địa kiêm chuyên gia ẩm thực & văn hóa địa phương.

QUY TẮC:
1. Ưu tiên dùng Context (dữ liệu Neo4j + Milvus) nếu có thông tin liên quan.
2. Nếu Context không đủ, dùng kiến thức sẵn có của bạn về ẩm thực, văn hóa, lịch sử miền Trung Việt Nam để trả lời.
3. KHÔNG BAO GIỜ bịa thông tin lịch sử không chắc chắn (năm xây dựng, đời Vua cụ thể, sự tích chưa xác nhận).
4. Khi dùng Context, trích nguồn [Nguồn: Milvus/Neo4j] nếu confidence > 0.7.
5. Khi dùng kiến thức chung, ghi rõ "(kiến thức chung)" để người dùng biết.
6. Trả lời bằng tiếng Việt, thân thiện, chi tiết, thực tế và hữu ích — như một người bạn địa phương.
7. Với câu hỏi ẩm thực: liệt kê cụ thể món ăn, mô tả hương vị, gợi ý nơi thử."""

    SYSTEM_PROMPT_GENERAL = """Bạn là LUNA — Trợ lý du lịch AI thông minh cho miền Trung Việt Nam.
Bạn giúp du khách tìm địa điểm, ẩm thực, lên kế hoạch du lịch.
Trả lời bằng tiếng Việt, thân thiện và hữu ích.
Sử dụng Context bên dưới để trả lời chính xác."""

    def __init__(self, neo4j_client: Neo4jClient, milvus_client: MilvusClient, mongo_client: MongoDBClient, trend_agent=None):
        self.neo4j = neo4j_client
        self.milvus = milvus_client
        self.mongo = mongo_client
        self.trend_agent = trend_agent
        
        # Use Application Default Credentials (ADC) since user is authenticated locally
        self.has_llm = True
        try:
            self.llm = genai.Client(vertexai=True, project=settings.vertex_ai_project, location=settings.vertex_ai_location)
        except Exception as e:
            logger.error(f"Failed to initialize Vertex AI Client: {e}")
            self.has_llm = False
            self.llm = None

    def process(self, user_id: str, message: str, session_id: str,
                context: dict | None = None) -> ChatResult:
        """
        Main chat processing pipeline.
        1. Classify intent → Route to Historian/Reporter/Itinerary
        2. Dual Retrieval (Vector + Graph)
        3. LLM Synthesis with context
        4. Save to chat history
        """
        intent = classify_query_intent(message)
        logger.info(f"🔀 Intent classified: {intent} for message: '{message[:50]}...'")

        # Save user message to history
        self.mongo.save_chat_message(
            user_id=user_id,
            session_id=session_id,
            role="user",
            content=message,
            context=context or {},
        )

        if intent == "historian":
            result = self._process_historian(message, context, session_id)
        elif intent == "reporter":
            result = self._process_reporter(message, context)
        elif intent == "itinerary":
            result = self._process_itinerary_query(message, context, session_id)
        else:
            result = self._process_historian(message, context, session_id)

        # Save assistant response to history
        self.mongo.save_chat_message(
            user_id=user_id,
            session_id=session_id,
            role="assistant",
            content=result.reply,
            source=result.source_type,
            context=context or {},
        )

        return result

    def _process_historian(self, message: str, context: dict | None, session_id: str) -> ChatResult:
        """
        Nhà Sử Học — Dual Retrieval Pipeline.
        Step 1: Vector Search (Milvus) — semantic similarity
        Step 2: Graph Traversal (Neo4j) — entity relationships
        Step 3: LLM synthesis with both contexts
        """
        sources = []

        # === Step 1: Vector Search (Milvus) ===
        query_embedding = embed_text(message)
        vector_results = self.milvus.search_locations(
            query_embedding=query_embedding,
            min_trust=0.6,
            limit=5,
        )
        vector_context = self._format_vector_results(vector_results)

        # === Step 2: Graph Traversal (Neo4j) ===
        graph_results = self.neo4j.traverse_for_context(message)
        graph_context = self._format_graph_results(graph_results)

        # Collect sources
        for vr in vector_results:
            sources.append({
                "title": vr.get("name", "Unknown"),
                "url": "",
                "confidence": vr.get("similarity", 0.0),
            })

        # === Step 3: LLM Synthesis with Conversation History ===
        combined_context = f"""
=== DỮ LIỆU TỪ VECTOR SEARCH (Milvus) ===
{vector_context}

=== DỮ LIỆU TỪ KNOWLEDGE GRAPH (Neo4j) ===
{graph_context}

=== NGỮ CẢNH HIỆN TẠI ===
Vị trí: {context.get('current_location', 'Không rõ') if context else 'Không rõ'}
Thời tiết: {context.get('weather_condition', 'Không rõ') if context else 'Không rõ'}
Thời điểm: {context.get('time_of_day', 'Không rõ') if context else 'Không rõ'}
Phương tiện: {context.get('transport_mode', 'Không rõ') if context else 'Không rõ'}
Chân dung du khách (MongoDB / Demographics): {context.get('persona_profile', 'Không có') if context else 'Không có'}
"""

        # Build contents array including history
        contents = []
        if session_id:
            try:
                history_data = self.mongo.get_chat_history(session_id, limit=5)
                messages = history_data.get("messages", [])
                
                # We skip the very last message in history if it's the current user message
                # to avoid duplication, though usually get_chat_history is called before save
                # But we already saved the user message at process() start!
                # So we take messages except the last one if it matches the current
                
                history_to_use = messages[:-1] if messages and messages[-1].get("content") == message else messages

                for msg in history_to_use:
                    role = "user" if msg.get("role") == "user" else "model"
                    contents.append(
                        types.Content(role=role, parts=[types.Part.from_text(text=msg.get("content", ""))])
                    )
            except Exception as e:
                logger.warning(f"Failed to load history for LLM: {e}")

        # Append current user query with context
        contents.append(
            types.Content(role="user", parts=[types.Part.from_text(text=f"Context:\n{combined_context}\n\nCâu hỏi: {message}")])
        )

        if self.has_llm and self.llm:
            # Historian: RAG context + Gemini's own knowledge. No Google Search —
            # Search is reserved for the Trend Hunter (reporter) per system design.
            config = types.GenerateContentConfig(
                system_instruction=self.SYSTEM_PROMPT_HISTORIAN,
                temperature=1,
                top_p=0.95,
                max_output_tokens=65535,
                safety_settings=[
                    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
                    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
                    types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
                    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF"),
                ],
            )
            try:
                reply = "Xin lỗi, tôi không thể tạo câu trả lời lúc này."
                response = self.llm.models.generate_content(
                    model=settings.gemini_model,
                    contents=contents,
                    config=config
                )
                reply = response.text if response.text else reply

            except Exception as e:
                logger.error(f"Historian LLM generation failed: {e}")
                reply = self._build_context_reply(message, vector_results, graph_results)
        else:
            # Fallback: format context directly
            reply = self._build_context_reply(message, vector_results, graph_results)

        return ChatResult(
            reply=reply,
            source_type="historian",
            sources=sources,
        )

    def _process_reporter(self, message: str, context: dict | None) -> ChatResult:
        """Nhà Báo — call TrendAgent directly with Google Search."""
        # Extract location from context or message keywords
        location = (context or {}).get("currentLocation", "")
        if not location:
            msg_lower = message.lower()
            if "huế" in msg_lower or "hue" in msg_lower:
                location = "Huế"
            elif "hội an" in msg_lower or "hoi an" in msg_lower:
                location = "Hội An"
            else:
                location = "Đà Nẵng"  # default

        if self.trend_agent:
            try:
                trend_result = self.trend_agent.search(query=message, location=location)
                places = trend_result.results
                if places:
                    lines = [f"**🔥 XU HƯỚNG TẠI {location.upper()}:**\n"]
                    for i, p in enumerate(places[:8], 1):
                        lines.append(f"{i}. **{p.name}** ({p.category})\n   {p.description}")
                        if p.source_url:
                            lines.append(f"   🔗 {p.source_url}")
                    reply = "\n".join(lines)
                else:
                    reply = f"Hiện chưa tìm được xu hướng mới cho '{message}' tại {location}. Thử lại sau nhé!"
            except Exception as e:
                logger.error(f"Reporter TrendAgent failed: {e}")
                reply = "Xin lỗi, không thể tìm kiếm xu hướng lúc này."
        else:
            reply = "Tính năng tìm kiếm xu hướng chưa khả dụng."

        return ChatResult(reply=reply, source_type="reporter", sources=[])

    def _process_itinerary_query(self, message: str, context: dict | None, session_id: str) -> ChatResult:
        """Process itinerary-related questions (not full generation)."""
        query_embedding = embed_text(message)
        vector_results = self.milvus.search_locations(
            query_embedding=query_embedding,
            limit=10,
        )

        places_context = "\n".join([
            f"- {r['name']} ({r['category']}, {r['environment']})"
            for r in vector_results
        ])

        if self.has_llm and self.llm:
            config = types.GenerateContentConfig(
                system_instruction=self.SYSTEM_PROMPT_GENERAL,
                temperature=1,
                top_p=0.95,
                max_output_tokens=65535,
                safety_settings=[
                    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
                    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
                    types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
                    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF")
                ],
            )
            try:
                contents = [
                    types.Content(role="user", parts=[types.Part.from_text(text=f"Địa điểm phù hợp:\n{places_context}\n\nCâu hỏi: {message}")])
                ]
                reply = "Xin lỗi, tôi không thể tạo câu trả lời lúc này."
                response = self.llm.models.generate_content(
                    model=settings.gemini_model,
                    contents=contents,
                    config=config
                )
                reply = response.text if response.text else reply
            except Exception as e:
                logger.error(f"Itinerary LLM generation failed: {e}")
                reply = f"🗺️ Dựa trên câu hỏi của bạn, đây là các địa điểm phù hợp:\n\n{places_context}\n\n💡 Để có gợi ý chi tiết hơn, hãy dùng tính năng Tạo Lịch trình."
        else:
            reply = f"🗺️ Dựa trên câu hỏi của bạn, đây là các địa điểm phù hợp:\n\n{places_context}\n\n💡 Để có gợi ý chi tiết hơn, hãy dùng tính năng Tạo Lịch trình."

        return ChatResult(
            reply=reply,
            source_type="itinerary",
            sources=[],
        )

    def _build_context_reply(self, message: str, vector_results: list, graph_results: list) -> str:
        """Build a response from retrieved context without LLM (fallback)."""
        parts = [f"🌙 **LUNA** — Kết quả tìm kiếm cho: *\"{message}\"*\n"]

        if vector_results:
            parts.append("📍 **Địa điểm liên quan:**")
            for r in vector_results[:5]:
                name = r.get("name", "?")
                cat = r.get("category", "")
                env = r.get("environment", "")
                dist = r.get("district", "")
                score = r.get("similarity", 0)
                parts.append(f"  • **{name}** — {cat}, {env}, {dist} (độ phù hợp: {score:.0%})")

        if graph_results:
            parts.append("\n🔗 **Thông tin từ Knowledge Graph:**")
            for r in graph_results[:3]:
                entity = r.get("entity", "?")
                connections = r.get("connections", [])
                if connections:
                    conn_names = [c.get("related", "?") for c in connections[:3]]
                    parts.append(f"  • **{entity}** → liên quan: {', '.join(conn_names)}")
                else:
                    parts.append(f"  • **{entity}**")

        if not vector_results and not graph_results:
            parts.append("⚠️ Chưa tìm thấy thông tin trong cơ sở dữ liệu. Hãy chạy Data Pipeline trước.")

        parts.append("\n💡 *Cài đặt GOOGLE_CLOUD_API_KEY trong .env để có câu trả lời tự nhiên hơn.*")
        return "\n".join(parts)

    def _format_vector_results(self, results: list) -> str:
        """Format Milvus search results into text context."""
        if not results:
            return "Không tìm thấy kết quả vector phù hợp."

        lines = []
        for r in results:
            lines.append(
                f"📍 {r['name']} | Loại: {r.get('category', 'N/A')} | "
                f"Khu vực: {r.get('district', 'N/A')} | "
                f"Môi trường: {r.get('environment', 'N/A')} | "
                f"Độ tin cậy: {r.get('trust_rank', 0):.2f} | "
                f"Tương đồng: {r.get('similarity', 0):.3f}"
            )
        return "\n".join(lines)

    def _format_graph_results(self, results: list) -> str:
        """Format Neo4j graph traversal results into text context."""
        if not results:
            return "Không tìm thấy dữ liệu đồ thị liên quan."

        lines = []
        for r in results:
            entity = r.get("entity", "Unknown")
            labels = r.get("labels", [])
            connections = r.get("connections", [])
            conn_str = ", ".join([
                f"{c.get('related', '?')} ({c.get('type', '?')})"
                for c in connections if c.get("related")
            ])
            lines.append(f"🔗 {entity} [{', '.join(labels)}] → {conn_str}")
        return "\n".join(lines)
