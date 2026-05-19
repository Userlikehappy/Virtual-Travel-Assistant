# 📐 TIÊU CHUẨN LẬP TRÌNH VÀ CẤU TRÚC ỨNG DỤNG (CODING STANDARDS)

Để đảm bảo dự án LUNA có thể mở rộng (Scalable), dễ bảo trì (Maintainable) và dễ dàng làm việc nhóm, toàn bộ source code của các Microservices (Backend NestJS, AI Engine Python, v.v.) phải tuân thủ nghiêm ngặt các nguyên tắc thiết kế dưới đây. Kiến trúc vĩ mô đã vững thì cấu trúc vi mô (Code-level) cũng phải chuẩn mực.

## 1. Nguyên lý Thiết kế Module (Module-Based Architecture)
Tất cả các tính năng của hệ thống phải được chia thành các **Module** độc lập. Không được phép viết code kiểu nguyên khối (Monolithic spaghetti).

*   **Tính Đóng Gói (Encapsulation):** Mỗi module (VD: `weather`, `itinerary`, `trend_hunter`) phải chứa toàn bộ các file liên quan đến nó (Controllers, Services, Repositories).
*   **Điểm Giao Tiếp Duy Nhất (Unified Entry Point):** Mỗi thư mục / Service nhỏ phải có một file xuất (Export) duy nhất hoặc một file Server gốc (VD: `main.py` ở Python, hoặc `app.module.ts` ở NestJS) làm nhiệm vụ tập hợp (Assemble) các module nhánh thành một Tree (Cây) thống nhất. Không được phép để các file trôi nổi ngoài cấu trúc cây.

## 2. Nguyên tắc Phân tách Trách nhiệm (Separation of Concerns)
Đây là quy tắc **SỐNG CÒN** để code không bị biến thành "cám heo". Tuyệt đối tuân thủ mô hình **Thin Controller / Fat Service** (Controller mỏng / Service dày).

### 2.1. Tầng Server / Controller (Lớp Giao tiếp)
Đại diện là các file `server.py`, `app.py`, hoặc các `@Controller` trong NestJS.
*   **Chỉ định tuyến và Nhận tham số (Routing & Parsing):** File này CHỈ làm nhiệm vụ lắng nghe Request tới, parse dữ liệu đầu vào.
*   **KHÔNG có Business Logic:** Tuyệt đối **cấm** viết các vòng lặp xử lý logic, tính toán điểm số (Scoring), gọi DB trực tiếp hay xử lý dữ liệu phức tạp đè vào file Server này.
*   **Giao việc:** Nhiệm vụ của file Server là lập tức gọi hàm (Call function) từ tầng Service tương ứng, nhận kết quả và bọc thành Response trả về.

### 2.2. Tầng Service / Provider (Lớp Nghiệp vụ)
Lớp này chứa các file `[Tên_chức_năng].service.ts` hoặc `[function_name]_logic.py`.
*   Nơi đây chứa **toàn bộ trí tuệ** của hệ thống (Business Logic). 
*   **Nguyên tắc Độc Lập:** Hàm logic phải được viết sao cho có thể được tái sử dụng bởi bất kỳ đâu (nhận input, trả output) mà không cần bận tâm nó được gọi từ gRPC, REST hay RabbitMQ.

### 2.3. Ví dụ Minh họa: Đúng vs Sai

**✅ ĐÚNG — NestJS (Thin Controller / Fat Service):**
```typescript
// chat.controller.ts — CHỈ routing và trả response
@Controller('chat')
export class ChatController {
  constructor(private readonly chatService: ChatService) {}

  @Post()
  async sendMessage(@Body() dto: ChatMessageDto) {
    const result = await this.chatService.processMessage(dto);
    return { success: true, data: result };
  }
}

// chat.service.ts — TOÀN BỘ LOGIC Ở ĐÂY
@Injectable()
export class ChatService {
  constructor(
    private readonly grpcClient: LunaAIGrpcClient,
    private readonly mqProducer: TrendSearchProducer,
  ) {}

  async processMessage(dto: ChatMessageDto): Promise<ChatResponse> {
    const intent = this.classifyIntent(dto.message);
    if (intent === 'history') {
      return this.grpcClient.chat(dto);        // gRPC sync
    } else if (intent === 'trend') {
      await this.mqProducer.publish(dto);       // RabbitMQ async
      return { reply: 'Đang tìm kiếm xu hướng...' };
    }
  }
}
```

**❌ SAI — Nhồi logic vào Controller:**
```typescript
// ❌ THUÀ ĐÔI KHÔNG LÀM THẾ NÀY
@Controller('chat')
export class ChatController {
  @Post()
  async sendMessage(@Body() dto: ChatMessageDto) {
    // ❌ Gọi DB trực tiếp trong Controller
    const user = await this.userRepo.findOne(dto.userId);
    // ❌ Tính toán Score trong Controller  
    const score = user.preferences.map(p => p.weight * 0.5);
    // ❌ Gọi API bên ngoài trong Controller
    const weather = await axios.get('https://api.weather...');
    // ... 200 dòng logic nữa → "cám heo"
  }
}
```

**✅ ĐÚNG — Python gRPC (Thin Server / Fat Logic):**
```python
# grpc_server/server.py — CHỈ nhận request và gọi service
class LunaAIServicer(luna_pb2_grpc.LunaAIServicer):
    def __init__(self, chat_logic, itinerary_logic):
        self.chat_logic = chat_logic
        self.itinerary_logic = itinerary_logic

    def Chat(self, request, context):
        result = self.chat_logic.process(request)  # Giao việc
        return luna_pb2.ChatResponse(reply=result.reply)

# services/chat_logic.py — TOÀN BỘ LOGIC Ở ĐÂY
class ChatLogic:
    def __init__(self, rag_engine, neo4j_client, milvus_client):
        self.rag = rag_engine
        self.neo4j = neo4j_client
        self.milvus = milvus_client

    def process(self, request) -> ChatResult:
        # Dual Retrieval: Vector Search + Graph Traversal
        vectors = self.milvus.search(request.message)
        graph_data = self.neo4j.traverse(request.message)
        return self.rag.generate(vectors, graph_data)
```

## 3. Cấu hình Máy chủ Cơ sở hạ tầng (Infrastructure Configuration)
Bất kể một dịch vụ mạng nào được dựng lên (gRPC, MQ) đều phải tường minh trong việc cấp phát tài nguyên sinh học của Server.

### 3.1. Cấu hình gRPC Server & Worker
Không được phép chạy gRPC server với các thông số mặc định ẩn dấu. Phải quy định rõ rệt:
*   **Ports & Host:** Cấu hình rõ ràng địa chỉ IP và Port lắng nghe.
*   **Workers Pool:** Server gRPC phải được thiết lập Thread Pool / Worker rõ ràng. Ví dụ: Set giới hạn là `max_workers=10` để tránh bị Overload (Tràn bộ nhớ) khi có đột biến Traffic.

### 3.2. Cấu hình Message Broker (RabbitMQ / Kafka)
*   **Khai báo Topic/Queue rõ ràng:** Mọi Exchange, Topic, và Queue đều phải có hằng số (Constants) tên riêng rạch ròi.
    ```typescript
    // message-queue/constants.ts
    export const MQ_EXCHANGES = {
      SEARCH: 'luna.search',
      PIPELINE: 'luna.pipeline',
      WEATHER: 'luna.weather',
    } as const;

    export const MQ_QUEUES = {
      TREND_SEARCH_TASKS: 'trend.search.tasks',
      TREND_SEARCH_RESULTS: 'trend.search.results',
      DEAD_LETTER: 'dead.letter.queue',
    } as const;
    ```
*   **Cơ chế Acknowledgements (Ack):** Phải xử lý logic báo nhận (Manual Ack) để đảm bảo không mất Message khi có một microservice bị sập giữa chừng.
*   **Re-try & Dead Letter Queue:** Cấu hình giới hạn số lần thử lại (VD: 3 lần), nếu lỗi vẫn lặp lại thì đẩy rác vào Dead Letter Queue để Developer debug sau, tránh bị dồn ứ (Block) hàng đợi chính.

## 4. Quản lý Biến Môi trường (Environment Variables)
*   **Nguyên tắc Không Hard-code:** Tuyệt đối cấm viết cứng (Hard-code) bất kỳ thông tin nhạy cảm (API Keys, Token, Passwords), cấu hình Port/Host (RabbitMQ host, Database URI, gRPC Ports) hay các con số tỷ lệ (Thresholds/Weights) vào trong mã nguồn.
*   **Luân chuyển qua `.env`:** Toàn bộ hằng số cấu hình hệ thống **PHẢI** được khai báo tại file `.env` ẩn ở thư mục gốc của mỗi Microservice.
*   **File Template `env.example`:** Mọi project con (Frontend, Backend, AI Engine) đều bắt buộc phải có một file `.env.example` liệt kê danh sách các Keys cần có (trống Value) để bất kỳ Developer nào khi Clone dự án về cũng hiểu ngay cần chuẩn bị các môi trường nào.
*   **Ví dụ Load Config (Python):**
    ```python
    # config/settings.py
    from pydantic_settings import BaseSettings

    class Settings(BaseSettings):
        # gRPC
        grpc_host: str = "0.0.0.0"
        grpc_port: int = 50051
        grpc_max_workers: int = 10

        # Neo4j
        neo4j_uri: str
        neo4j_user: str
        neo4j_password: str

        # Milvus
        milvus_uri: str

        # LLM
        openai_api_key: str
        tavily_api_key: str

        # RabbitMQ
        rabbitmq_url: str

        # Redis
        redis_url: str = "redis://localhost:6379"

        class Config:
            env_file = ".env"

    settings = Settings()  # Tự động load từ .env, báo lỗi nếu thiếu key
    ```

## 5. Tổ chức Thư mục Chuẩn (Standard Folder Structure)
Ví dụ điển hình cho tầng Trí tuệ Nhân tạo (AI Engine):

```text
ai-engine/
├── .env                  # (Bảo mật) Chứa toàn bộ cấu hình Port, API Keys, DB URI, RabbitMQ Host.
├── .env.example          # Template danh sách các biến cần.
├── requirements.txt      # Khai báo thư viện (hoặc Pipfile / pyproject.toml)
├── protos/               # Nơi chứa các hợp đồng giao tiếp .proto
├── src/
│   ├── config/           # Loader và Validator cho các biến từ file .env
│   ├── grpc_server/      # Code khởi chạy gRPC thuần (Thin layer, setup Ports, Workers)
│   ├── message_queue/    # Consumers / Producers kết nối RabbitMQ (Lắng nghe sự kiện)
│   ├── services/         # Fat layer - Chứa lõi Logic, thuật toán tính điểm, LightRAG.
│   ├── clients/          # Các class chuyên đi gọi hệ thống khác (Tavily HTTP Client, Neo4j Driver)
│   └── utils/            # Helper functions, formatters (JSON parsing)
└── main.py               # Entry point duy nhất (Root). Chỉ chứa vài dòng code để assemble và run gRPC & MQ listeners.
```
