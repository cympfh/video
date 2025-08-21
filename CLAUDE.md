# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a FastAPI-based video URL conversion service designed for VRChat video players. The service provides a simple redirect API that converts URLs from various video platforms (Bilibili, NicoNico) to VRChat-compatible formats.

## Architecture

- **Single Python file**: `video-server.py` - Contains the entire FastAPI application
- **Main endpoint**: `/video?url={VIDEO_URL}` - Accepts video URLs and redirects to converted formats
- **Core function**: `convert()` - Handles URL transformation logic for different platforms

### URL Conversion Logic
- **NicoNico**: Converts `nicovideo.jp/watch/` URLs to `nicovideo.life/watch?v=` format
- **Bilibili**: Wraps URLs with `biliplayer.91vrchat.com/player/?url=` 
- **Other platforms**: Returns URLs unchanged (e.g., YouTube)

## Development Commands

### Running the server locally
```bash
pip install "fastapi[standard]"
fastapi run video-server.py --host 0.0.0.0 --port 8080
```

### Docker deployment
```bash
docker build -t video-server .
docker run -p 8080:8080 video-server
```

## Testing

The `convert()` function includes comprehensive docstring examples that serve as documentation and can be used for testing the URL conversion logic.

## Dependencies

- FastAPI with standard extras (includes uvicorn for ASGI server)
- Python 3.12 (as specified in Dockerfile)