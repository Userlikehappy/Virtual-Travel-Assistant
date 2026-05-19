FROM python:3.12-slim
WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download embedding model so first startup doesn't need internet access
# Model is cached inside the image layer (~500MB)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('paraphrase-multilingual-mpnet-base-v2'); print('Model cached OK')"

# Copy application code (includes data/seed/ for auto-seeding)
COPY . .

ENV PYTHONPATH=/app
EXPOSE 50051
CMD ["python", "main.py"]
