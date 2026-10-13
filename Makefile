build:
	docker build -t video:latest .

run:
	docker run --rm -e YOUTUBE_API_KEY=${YOUTUBE_API_KEY} -e FORWARDED_ALLOW_IPS='*' -p 8080:8080 video:latest
