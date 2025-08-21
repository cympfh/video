build:
	docker build -t video:latest .

run:
	docker run --rm -e YOUTUBE_API_KEY=${YOUTUBE_API_KEY} -p 8080:8080 video:latest
