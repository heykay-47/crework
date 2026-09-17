FROM python:3.14-slim

RUN apt-get update \
    && apt-get install --yes --no-install-recommends espeak-ng ffmpeg fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY Dockerfile pyproject.toml uv.lock ./
COPY feedback_triage ./feedback_triage
COPY main.py ./
RUN pip install --no-cache-dir .

ENTRYPOINT ["python", "main.py"]
