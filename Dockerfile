FROM python:3.12

RUN apt-get update && apt-get install -y \
    ffmpeg \
    imagemagick \
    ca-certificates

# yt-dlp の YouTube JS challenge 用
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /app
RUN pip install uv
COPY . .
RUN uv sync

ENV YOUTUBE_COOKIES=/home/ubuntu/firefox/cookie.txt

CMD ["uv", "run", "fastapi", "run", "video-server.py", "--host", "0.0.0.0", "--port", "8080"]
