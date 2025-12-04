FROM python:3.11-alpine

RUN apk add --no-cache socat ca-certificates \
    && pip install --no-cache-dir requests

WORKDIR /app
COPY main.py .

CMD ["python", "-u", "main.py"]