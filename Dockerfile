FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py tickets.json study.html ./
COPY static/ ./static/

RUN mkdir -p /app/data

ENV DATA_DIR=/app/data
ENV PORT=8080

EXPOSE 8080

CMD ["python", "server.py"]
