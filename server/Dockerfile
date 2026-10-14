# The Bet Builder server for the iPhone app. Build from the repository root:
#   docker build -f server/Dockerfile -t bet-builder-server .
FROM python:3.12-slim
WORKDIR /app
COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt
COPY . .
ENV NFL_EDGE_CACHE=/data/cache DATA_DIR=/data/server PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn server.app:app --host 0.0.0.0 --port ${PORT}"]
