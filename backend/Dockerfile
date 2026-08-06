FROM python:3.11-slim

# P2-B4: Set timezone Asia/Shanghai
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app

# Install system dependencies (required by asyncmy)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc default-libmysqlclient-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first (to leverage Docker layer cache)
COPY pyproject.toml ./

# Install Python dependencies
RUN pip install --no-cache-dir \
    asyncmy cryptography "fastapi[standard]" huggingface-hub jieba \
    "langchain>=1.2.14" "langchain-openai" "langgraph>=1.1.4" \
    loguru omegaconf pyyaml "pymilvus>=2.4.0" "sqlalchemy>=2.0.48" \
    pydantic-settings dashscope redis PyJWT kafka-python prometheus_client

# Copy project files (P2-B3: prompts/conf must be included)
COPY app/ ./app/
COPY conf/ ./conf/
COPY prompts/ ./prompts/
COPY main.py ./

# Log directory
RUN mkdir -p logs

EXPOSE 8000

# Health check (P2-C1/C2: standalone short timeout)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)" || exit 1

ENV UVICORN_WORKERS=2
CMD ["sh", "-c", "python -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-2}"]
