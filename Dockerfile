FROM python:3.12

RUN apt-get update && apt-get install -y \
    ffmpeg \
    imagemagick

WORKDIR /app
RUN pip install uv
COPY . .
RUN uv sync

# nginx is not 127.0.0.1, so the default trust list would ignore
# X-Forwarded-Proto. fastapi run enables --proxy-headers and reads this env.
ENV FORWARDED_ALLOW_IPS=*

CMD ["uv", "run", "fastapi", "run", "video-server.py", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
