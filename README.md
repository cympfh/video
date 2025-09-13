# Video Service

A FastAPI-based multi-media service designed for VRChat video players and content sharing.

## Usage

### Video URLs
```
http://s.cympfh.cc/?url={VIDEO_URL}
```

**Supported Video Platforms:**
- **YouTube** - `http://s.cympfh.cc/?url=https://www.youtube.com/watch?v=-fRA1CvuPXM`
  - Note: VRChat supports YouTube links directly, so conversion may not be necessary
- **Bilibili** - `http://s.cympfh.cc/?url=https://www.bilibili.com/video/BV1sNuDzsEjM`
  - Automatically wraps URLs for VRChat compatibility
- **NicoNico** - `http://s.cympfh.cc/?url=https://www.nicovideo.jp/watch/sm45154842`
  - Converts to VRChat-compatible format

### Images
```
http://s.cympfh.cc/?url={IMAGE_URL}
```
Supports any direct image URL (PNG, JPG, JPEG, GIF, WebP) or URLs that return image content.

### YouTube Search
```
http://s.cympfh.cc/?url=y!{keyword}
```
- `http://s.cympfh.cc/?url=y!cats` - Shows search results as an image
- `http://s.cympfh.cc/?url=y!cats!0` - Redirects to the first video result
- `http://s.cympfh.cc/?url=y!cats!1` - Redirects to the second video result

### Random Content
```
http://s.cympfh.cc/?url=random
```
Returns a random video from a curated list, updated hourly.

## Alternative Endpoint

All functionality is also available at:
```
http://s.cympfh.cc/video?url={URL}
```
