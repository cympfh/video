build:
	docker build -t video:latest .

run:
	docker run --rm -p 8080:8080 video:latest
