# Image du serveur papote (WebSocket + client web servi sur le même port).
# Seul `websockets` est nécessaire côté serveur (textual/rich = client terminal).
FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir "websockets>=12"
COPY papote/ ./papote/

# $PORT est fourni par l'hébergeur (défaut 8765). La base va dans un volume
# persistant si $PAPOTE_DB pointe dessus (ex: /data/server.db).
ENV PORT=8765 \
    PYTHONUNBUFFERED=1
EXPOSE 8765

CMD ["python", "-m", "papote.server"]
