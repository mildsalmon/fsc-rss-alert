FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY feed_collector ./feed_collector
COPY sources.yaml ./sources.yaml

RUN pip install --no-cache-dir .

ENTRYPOINT ["python", "-m", "feed_collector"]
CMD ["poll"]
