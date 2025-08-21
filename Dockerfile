FROM python:3.12

RUN apt-get update && apt-get install -y \
    ffmpeg \
    imagemagick

WORKDIR /app
RUN pip install uv
COPY . .
RUN uv sync

CMD ["uv", "run", "fastapi", "run", "video-server.py", "--host", "0.0.0.0", "--port", "8080"]
