FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

# Persistent data volume
VOLUME /data

ENV BOT_TOKEN=8868679973:AAHmzHA-ytNA4ZREhQNuQxk3JhCcvQ_toYg
ENV CHAT_ID=5446910738
ENV EXTRACTOR_URL=http://okzr3huc0sxjmwk5ywcohdqp.130.61.171.201.sslip.io
ENV PONO_URL=https://pono-w53e.onrender.com
ENV STATE_FILE=/data/bot_state.json
ENV PORT=3000

EXPOSE 3000

CMD ["python", "bot.py"]
