# LUNA TravelTech — Workflow & Demographic Flow Diagrams

---

## 1. Kiến trúc tổng thể (System Architecture)

```mermaid
graph TB
    subgraph CLIENT["🖥️ CLIENT LAYER"]
        FE["Frontend<br/>Next.js 16 · React 19<br/>:3001"]
    end

    subgraph GATEWAY["🔀 API GATEWAY LAYER"]
        BE["Backend<br/>NestJS 11 · TypeScript<br/>:3000"]
    end

    subgraph AI["🤖 AI ENGINE LAYER"]
        AIE["AI Engine<br/>Python · gRPC Server<br/>:50051"]
    end

    subgraph QUEUE["📨 MESSAGE BROKER"]
        MQ["RabbitMQ<br/>AMQP · :5672"]
    end

    subgraph STORES["🗄️ DATA STORES"]
        MONGO["MongoDB<br/>Users · Chat · Itinerary<br/>:27017"]
        REDIS["Redis<br/>Cache · Session<br/>:6379"]
        NEO["Neo4j<br/>Knowledge Graph<br/>:7687"]
        MIL["Milvus<br/>Vector DB<br/>:19530"]
    end

    subgraph EXTERNAL["🌐 EXTERNAL APIs"]
        OWM["OpenWeatherMap API"]
        GEM["Google Gemini LLM"]
        TAV["Tavily Web Search"]
    end

    FE -- "REST · JSON" --> BE
    FE -- "WebSocket (Socket.io)" --> BE
    BE -- "gRPC · Protobuf" --> AIE
    BE -- "AMQP publish" --> MQ
    AIE -- "AMQP consume/publish" --> MQ
    BE -- "Mongoose ODM" --> MONGO
    BE -- "ioredis" --> REDIS
    AIE -- "pymongo" --> MONGO
    AIE -- "neo4j driver" --> NEO
    AIE -- "pymilvus" --> MIL
    AIE -- "redis-py" --> REDIS
    BE -- "HTTP polling" --> OWM
    AIE -- "google-genai SDK" --> GEM
    AIE -- "httpx" --> TAV
```

---

## 2. Luồng Nhân Khẩu Học — Từ Đăng Ký đến Scoring

```mermaid
flowchart TD
    subgraph REGISTER["👤 ĐĂNG KÝ & ONBOARDING"]
        R1["User đăng ký<br/>email + password"]
        R2["Onboarding form:<br/>• Nhóm tuổi (ageGroup)<br/>• Ngân sách (budgetLevel: low/medium/high)<br/>• Phương tiện (transport: walk/motorbike/car)<br/>• Ăn kiêng (dietary[])<br/>• Sở thích (interests[])"]
        R3[("MongoDB users<br/>collection")]
    end

    subgraph CONTEXT["📡 RUNTIME CONTEXT"]
        C1["Frontend gửi request<br/>kèm context hiện tại"]
        C2["Backend lấy User Persona<br/>từ MongoDB"]
        C3["Merge Persona + Context:<br/>• currentLocation<br/>• weatherCondition<br/>• timeOfDay<br/>• transportMode"]
    end

    subgraph GRPC["📦 gRPC PAYLOAD"]
        G1["ItineraryRequest {<br/>  preferences: ['history','food']<br/>  budget_level: 'medium'<br/>  transport_mode: 'motorbike'<br/>}"]
        G2["ChatRequest {<br/>  context: ContextInfo {<br/>    transport_mode: 'motorbike'<br/>    weather_condition: 'sunny'<br/>    time_of_day: 'morning'<br/>  }<br/>}"]
    end

    subgraph VECTOR["🔢 PERSONA VECTORIZATION"]
        V1["Tạo Persona Text:<br/>'history food medium motorbike 25-35'"]
        V2["sentence-transformers<br/>paraphrase-multilingual-mpnet-base-v2"]
        V3["Persona Vector [384-dim]<br/>[0.245, -0.123, ..., 0.456]"]
        V4["Cosine Similarity<br/>vs. Location Vectors"]
        V5["persona_similarity ∈ [0, 1]"]
    end

    subgraph SCORE["⚖️ SCORING ENGINE"]
        S1["S_persona = persona_similarity"]
        S2["S_weather = weather_fit"]
        S3["S_trend = trend_score"]
        S4["S_culinary = cuisine_fit"]
        S5["S_logistics = distance_fit"]
        FORMULA["FinalScore =<br/>0.35 × S_persona<br/>+ 0.10 × S_weather<br/>+ 0.25 × S_trend<br/>+ 0.15 × S_culinary<br/>+ 0.15 × S_logistics<br/>− Penalties"]
    end

    R1 --> R2 --> R3
    R3 --> C2
    C1 --> C2 --> C3
    C3 --> G1
    C3 --> G2
    G1 --> V1
    V1 --> V2 --> V3 --> V4 --> V5
    V5 --> S1
    S1 & S2 & S3 & S4 & S5 --> FORMULA
```

---

## 3. Trọng Số Động — Persona Shifts theo Context

```mermaid
quadrantChart
    title Trọng số Scoring theo Weather Condition
    x-axis Low Weather Priority --> High Weather Priority
    y-axis Low Persona Priority --> High Persona Priority
    quadrant-1 Balanced (sunny)
    quadrant-2 Persona-first
    quadrant-3 Logistics-first
    quadrant-4 Safety-first (storm)
    Sunny Day: [0.2, 0.7]
    Cloudy: [0.4, 0.55]
    Rainy: [0.65, 0.35]
    Stormy: [0.85, 0.2]
```

```mermaid
bar
    title Phân bổ trọng số theo điều kiện thời tiết
    x-axis ["Persona", "Weather", "Trend", "Culinary", "Logistics"]
    y-axis "Trọng số (%)" 0 --> 50
    bar [35, 10, 25, 15, 15] "☀️ Sunny (default)"
    bar [20, 40, 10, 15, 15] "⛈️ Stormy (safety-first)"
    bar [15, 10, 45, 15, 15] "🔥 Trend Query"
```

---

## 4. Use Case A — Chat (Nhà Sử Học)

```mermaid
sequenceDiagram
    actor User
    participant FE as Frontend<br/>(Next.js)
    participant BE as Backend<br/>(NestJS)
    participant MONGO as MongoDB
    participant AIE as AI Engine<br/>(Python)
    participant MIL as Milvus<br/>(Vector DB)
    participant NEO as Neo4j<br/>(Graph DB)
    participant LLM as Google Gemini

    User->>FE: Nhập câu hỏi<br/>"Đại Nội Huế mở cửa mấy giờ?"
    FE->>BE: socket.emit('chat:message', {message, userId})

    BE->>MONGO: findById(userId)
    MONGO-->>BE: {transport: 'motorbike', interests: ['history']}

    Note over BE: Merge Persona + Context
    BE->>AIE: gRPC Chat({<br/>  message, userId,<br/>  context: {transport_mode, location, weather}<br/>})

    AIE->>MIL: search(embed("Đại Nội Huế"), limit=5)
    MIL-->>AIE: [doc1: 0.92, doc2: 0.88, ...]

    AIE->>NEO: MATCH (h:HERITAGE {name:"Đại Nội Huế"})<br/>RETURN h.operating_hours, h.description
    NEO-->>AIE: {operating_hours: "07:00-17:30", ticket: 200k}

    Note over AIE: Build LLM Prompt với<br/>Personas + Retrieved Context
    AIE->>LLM: prompt = personas + milvus_results + neo4j_results
    LLM-->>AIE: "Đại Nội Huế mở cửa 7:00-17:30..."

    AIE-->>BE: ChatResponse{reply, sources, sourceType}
    BE->>BE: Lưu chat log → MongoDB

    BE->>FE: socket.emit('chat:response', {reply, sources})
    FE->>User: Hiển thị câu trả lời + citations
```

---

## 5. Use Case B — Tạo Lịch Trình (TSP + Persona Scoring)

```mermaid
sequenceDiagram
    actor User
    participant FE as Frontend
    participant BE as Backend
    participant MONGO as MongoDB
    participant REDIS as Redis
    participant AIE as AI Engine
    participant MIL as Milvus
    participant NEO as Neo4j
    participant SE as Scoring Engine
    participant OWM as OpenWeatherMap

    User->>FE: Submit form<br/>{destination, numDays, preferences, budget, transport}
    FE->>BE: POST /api/itinerary/generate

    BE->>MONGO: Lấy user persona
    MONGO-->>BE: {interests, budgetLevel, transport}

    BE->>OWM: GET weather forecast
    OWM-->>BE: {condition: "sunny", temp: 28}

    BE->>AIE: gRPC GenerateItinerary({<br/>  preferences: ['history','food'],<br/>  budget_level: 'medium',<br/>  transport_mode: 'motorbike'<br/>})

    Note over AIE: Phase 1: Candidate Selection
    AIE->>MIL: Embed("history food medium Đà Nẵng")<br/>→ search top 45 locations
    MIL-->>AIE: [loc_A:0.95, loc_B:0.90, loc_C:0.88...]

    AIE->>NEO: MATCH locations với relations<br/>(SERVES, LOCATED_IN, FEATURES)
    NEO-->>AIE: Enriched location data

    Note over AIE: Phase 2: Scoring với Persona
    loop Mỗi location candidate
        AIE->>SE: score_location(loc, ScoringContext{transport, weather})
        SE-->>AIE: FinalScore = 0.35×S_persona + ...
    end

    Note over AIE: Phase 3: TSP Optimization
    AIE->>AIE: Sắp xếp theo ngày/buổi<br/>Tối ưu khoảng cách + giờ mở cửa

    Note over AIE: Phase 4: Weather Adjustment
    AIE->>AIE: Nếu mưa → replace outdoor → indoor

    AIE-->>BE: ItineraryResponse{days[], total_cost}

    BE->>MONGO: Lưu itinerary
    BE->>REDIS: Cache itinerary (30 phút)
    BE-->>FE: Itinerary JSON

    FE->>User: Hiển thị Timeline + Map (Leaflet)
```

---

## 6. Use Case C — Weather Alert & Re-Routing

```mermaid
sequenceDiagram
    participant CRON as Backend Scheduler<br/>(every 15min)
    participant OWM as OpenWeatherMap
    participant BE as Backend
    participant AIE as AI Engine
    participant MONGO as MongoDB
    participant FE as Frontend
    actor User

    loop Mỗi 15 phút
        CRON->>OWM: Poll weather API
        OWM-->>CRON: {condition: "storm_warning"}

        CRON->>MONGO: Lấy active itineraries
        MONGO-->>CRON: [itinerary_1, itinerary_2, ...]

        CRON->>AIE: gRPC ReRouteItinerary({<br/>  itinerary_id,<br/>  new_weather: "storm_warning"<br/>})

        Note over AIE: Tìm indoor alternatives
        AIE->>AIE: WHERE environment IN ['indoor','sheltered']<br/>Re-score với w_weather = 0.40
        AIE-->>CRON: Updated ItineraryResponse

        CRON->>BE: Broadcast updated itinerary
        BE->>FE: socket.emit('weather:alert', {<br/>  alert: "⚠️ Mưa lớn!",<br/>  updatedItinerary: {...}<br/>})
        FE->>User: Hiển thị cảnh báo + lịch mới
    end
```

---

## 7. Use Case D — Trend Hunting (Async)

```mermaid
sequenceDiagram
    actor User
    participant FE as Frontend
    participant BE as Backend
    participant MQ as RabbitMQ
    participant AIE as AI Engine
    participant TAV as Tavily Search
    participant REDIS as Redis

    User->>FE: "Quán cà phê trending ở Đà Nẵng?"
    FE->>BE: POST /api/trend/search?query=café&location=Đà Nẵng

    Note over BE: Không chờ kết quả (async)
    BE->>MQ: publish("trend.hunt", {query, location, request_id:"abc123"})
    BE-->>FE: {status: "searching", request_id: "abc123"}

    Note over AIE: Consumer chạy nền
    AIE->>MQ: consume("trend.hunt")
    MQ-->>AIE: {query:"café", location:"Đà Nẵng", request_id:"abc123"}

    AIE->>TAV: Web search "trending café Đà Nẵng 2025"
    TAV-->>AIE: [result_1, result_2, result_3...]

    AIE->>AIE: Score kết quả:<br/>• Recency (mới nhất)<br/>• Engagement (lượt thích)<br/>• Semantic similarity vs persona

    AIE->>REDIS: Cache results (1 giờ)
    AIE->>MQ: publish("trend.results", {request_id, results})

    BE->>MQ: consume("trend.results")
    MQ-->>BE: {request_id:"abc123", results:[...]}

    BE->>FE: socket.emit('trend:results', results)
    FE->>User: Hiển thị kết quả trending realtime
```

---

## 8. Data Pipeline — Khởi tạo Knowledge Base

```mermaid
flowchart LR
    subgraph CRAWL["🕷️ Step 1: Crawl"]
        C1["static_crawler.py"]
        C2["Di tích lịch sử<br/>Đại Nội, Chùa Thiên Mụ..."]
        C3["Nhà hàng, Ẩm thực<br/>Bún chả, Bánh mì..."]
        C4["data/raw/*.json<br/>~2000+ records"]
        C1 --> C2 & C3 --> C4
    end

    subgraph NEO4J_LOAD["🕸️ Step 2: Knowledge Graph"]
        N1["neo4j_loader.py"]
        N2["CREATE nodes:<br/>HERITAGE · LOCATION<br/>CUISINE · RESTAURANT"]
        N3["CREATE relationships:<br/>LOCATED_IN · SERVES<br/>FEATURES · NEAR"]
        N4[("Neo4j<br/>:7687")]
        N1 --> N2 & N3 --> N4
    end

    subgraph EMBED["🔢 Step 3: Vector Embedding"]
        E1["embedder.py"]
        E2["sentence-transformers<br/>paraphrase-multilingual-mpnet"]
        E3["Text → Vector [384-dim]"]
        E4[("Milvus<br/>:19530")]
        E1 --> E2 --> E3 --> E4
    end

    C4 --> N1
    C4 --> E1
```

---

## 9. Nhân Khẩu Học — Luồng Dữ Liệu Đầy Đủ End-to-End

```mermaid
flowchart TD
    subgraph USER_PROFILE["👤 User Persona (MongoDB)"]
        UP["users collection:<br/>• ageGroup: '25-35'<br/>• budgetLevel: 'medium'<br/>• transport: 'motorbike'<br/>• dietary: ['vegetarian']<br/>• interests: ['history','food']"]
    end

    subgraph FRONTEND_CTX["📱 Frontend Runtime Context"]
        FC["Context tức thời:<br/>• currentLocation: 'Đà Nẵng'<br/>• weatherCondition: 'sunny'<br/>• timeOfDay: 'morning'"]
    end

    subgraph BACKEND_MERGE["🔀 Backend — Merge Layer"]
        BM["Merged Payload:<br/>interests + budgetLevel + transport<br/>+ location + weather + timeOfDay"]
    end

    subgraph GRPC_PROTO["📦 gRPC Protobuf"]
        GP1["ItineraryRequest:<br/>preferences[] + budget_level + transport_mode"]
        GP2["ChatRequest:<br/>ContextInfo {transport, weather, location, time}"]
    end

    subgraph AI_PROCESSING["🤖 AI Engine Processing"]
        subgraph HISTORIAN["📚 Historian (Chat)"]
            H1["Embed message → Milvus search"]
            H2["Graph traversal Neo4j"]
            H3["Gemini LLM với persona context"]
        end
        subgraph ITINERARY["🗓️ Itinerary Generator"]
            I1["Embed persona text → candidate search"]
            I2["Scoring Engine<br/>FinalScore = 0.35×S_persona + ..."]
            I3["TSP Optimization"]
            I4["Weather Adjustment"]
        end
        subgraph TREND["🔥 Trend Agent"]
            T1["Web search + NLP"]
            T2["Score theo recency + semantic"]
        end
    end

    subgraph OUTPUT["📤 Output"]
        O1["💬 Chat Response<br/>+ Citations"]
        O2["🗺️ Itinerary + Map<br/>Timeline theo ngày"]
        O3["🔥 Trending Places<br/>Realtime push"]
    end

    UP --> BM
    FC --> BM
    BM --> GP1 & GP2
    GP1 --> I1 --> I2 --> I3 --> I4 --> O2
    GP2 --> H1 --> H3
    GP2 --> H2 --> H3
    H3 --> O1
    BM --> T1 --> T2 --> O3
```

---

## 10. Budget Levels & Transport Modes — Impact Map

```mermaid
mindmap
    root((Nhân Khẩu Học))
        Ngân sách
            low
                Max 50k/visit
                Loại bỏ luxury venues
                Ưu tiên quán bình dân
            medium
                Max 150k/visit
                Cân bằng trải nghiệm
                Default setting
            high
                Max 500k/visit
                Không lọc theo giá
                Ưu tiên premium
        Phương tiện
            walk
                Ưu tiên nearby
                Penalty khoảng cách > 500m
                Logistics weight tăng
            motorbike
                Flexible nhất
                Vào hẻm được
                Default setting
            car
                Cần bãi đỗ
                Ưu tiên đường lớn
                Logistics penalty cao hơn
        Sở thích
            history
                Ưu tiên di tích, bảo tàng
                S_persona cao với heritage
            food
                Ưu tiên nhà hàng, quán ăn
                S_culinary weight tăng
            nightlife
                Ưu tiên bar, club
                time_of_day evening/night
            beach
                Ưu tiên biển, hồ bơi
                environment outdoor
            check-in
                Ưu tiên view đẹp
                trending score tăng
```

---

## 11. Scoring Formula — Visual Breakdown

```mermaid
flowchart LR
    subgraph INPUTS["📥 Inputs"]
        I1["User Persona Vector<br/>[384-dim]"]
        I2["Location Vector<br/>[384-dim]"]
        I3["Weather: sunny/rain/storm"]
        I4["Trend Score (Tavily)"]
        I5["Cuisine Match"]
        I6["Distance / Hours"]
    end

    subgraph COMPONENTS["⚙️ Score Components"]
        S1["S_persona<br/>= cosine(persona, location)"]
        S2["S_weather<br/>= weather fit score"]
        S3["S_trend<br/>= recency × engagement"]
        S4["S_culinary<br/>= cuisine match"]
        S5["S_logistics<br/>= 1 - distance_penalty"]
    end

    subgraph WEIGHTS["⚖️ Weights (Sunny)"]
        W1["× 0.35"]
        W2["× 0.10"]
        W3["× 0.25"]
        W4["× 0.15"]
        W5["× 0.15"]
    end

    subgraph FINAL["🏆 Final Score"]
        F1["Σ weighted scores<br/>− budget_penalty<br/>− hours_penalty<br/>= FinalScore ∈ [0,1]"]
    end

    I1 & I2 --> S1
    I3 --> S2
    I4 --> S3
    I5 --> S4
    I6 --> S5

    S1 --> W1
    S2 --> W2
    S3 --> W3
    S4 --> W4
    S5 --> W5

    W1 & W2 & W3 & W4 & W5 --> F1
```
