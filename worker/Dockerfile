# Cloud fallback worker (optional): runs the same worker with espeak-ng as the voice and FFmpeg from the distro.
# Deploy as its own service (never inside the web API). Set PRICECHAT_SERVER and PRICECHAT_WORKER_TOKEN
# (and ANTHROPIC_API_KEY / OPENAI_API_KEY for cloud script writing / voice).
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg espeak-ng fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir httpx pillow
COPY pricechat_worker ./pricechat_worker
USER nobody
CMD ["python", "-m", "pricechat_worker"]
