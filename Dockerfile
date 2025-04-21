FROM python:3.12

WORKDIR /app
RUN pip install "fastapi[standard]"
COPY ./video-server.py .

CMD ["fastapi", "run", "video-server.py", "--host", "0.0.0.0", "--port", "8080"]
