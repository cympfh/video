build:
	docker build -t video:latest .

run:
	docker run --rm \
		-e YOUTUBE_API_KEY=${YOUTUBE_API_KEY} \
		-v /home/ubuntu/firefox/cookie.txt:/home/ubuntu/firefox/cookie.txt:ro \
		-p 8080:8080 video:latest
