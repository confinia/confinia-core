FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

# Données montées en volume : ./data -> /data (voir docker-compose.yml)
VOLUME /data
WORKDIR /data

ENTRYPOINT ["python3"]
CMD ["/app/ingest_cog.py", "--help"]
