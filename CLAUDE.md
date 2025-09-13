# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a FastAPI-based multi-media service designed for VRChat video players. The service provides redirect APIs that handle video URLs, images, YouTube searches, and random video selection from various platforms.

## Architecture

- **Main server**: `video-server.py` - FastAPI application with multiple endpoints
- **Utility modules**: `util/` directory containing specialized handlers:
  - `youtube.py` - YouTube API integration with search and caching
  - `image_stream.py` - Image processing and streaming
  - `random.py` - Random video selection from GitHub Gist
- **Main endpoints**:
  - `/` and `/video` - Accept URL parameter for processing
  - Static file serving at `/video/stream`

### URL Processing Logic
The service automatically determines URL type through `UrlType.from_url()`:
- **Video URLs**: Converted via `convert()` function (NicoNico, Bilibili transformation)
- **Images**: Processed through `ImageStream`
- **YouTube Search**: `y!{keyword}` or `y!{keyword}!{index}` format
- **Random**: Returns random video from curated list
- **NicoNico**: Converts `nicovideo.jp/watch/` URLs to `nicovideo.life/watch?v=` format
- **Bilibili**: Wraps URLs with `biliplayer.91vrchat.com/player/?url=`

## Development Commands

### Local development with uv
```bash
uv sync
uv run fastapi run video-server.py --host 0.0.0.0 --port 8080
```

### Docker deployment
```bash
make build
make run  # Requires YOUTUBE_API_KEY environment variable
```

### Alternative local setup
```bash
pip install "fastapi[standard]"
fastapi run video-server.py --host 0.0.0.0 --port 8080
```

## Environment Variables

- **YOUTUBE_API_KEY**: Required for YouTube search functionality

## Testing

The `convert()` function includes comprehensive docstring examples that serve as documentation and can be used for testing the URL conversion logic.

## Dependencies

- FastAPI with standard extras (includes uvicorn for ASGI server)
- httpx for HTTP client operations
- System dependencies: ffmpeg, imagemagick (Docker only)
- Python 3.12+ (managed via uv)