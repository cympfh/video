# 🎬 s.cympfh.cc/video

Multi-media service for VRChat video players. Converts video URLs, streams images, and provides YouTube search functionality.

## 🚀 Usage

All features use the same endpoint:
```
http://s.cympfh.cc/video?url={URL}
```

### 📹 Video Platforms

**YouTube**
```
http://s.cympfh.cc/video?url=https://www.youtube.com/watch?v=-fRA1CvuPXM
```
Note: VRChat supports YouTube directly, so conversion may not be needed.

**Bilibili**
```
http://s.cympfh.cc/video?url=https://www.bilibili.com/video/BV1sNuDzsEjM
```
Wraps URLs for VRChat compatibility.

**NicoNico**
```
http://s.cympfh.cc/video?url=https://www.nicovideo.jp/watch/sm45154842
```
Converts to VRChat-compatible format.

**X (Twitter)**
```
http://s.cympfh.cc/video?url=https://x.com/pa_draws/status/1849228056537497835
```
Proxies video content for VRChat playback.

### 🖼️ Images

```
http://s.cympfh.cc/video?url={IMAGE_URL}
```
Converts images to live streams. Supports PNG, JPG, JPEG, GIF, WebP formats and URLs returning image content.

### 🔍 YouTube Search

**Search results as image:**
```
http://s.cympfh.cc/video?url=y!cats
```

**Direct video access:**
```
http://s.cympfh.cc/video?url=y!cats!0    # First result
http://s.cympfh.cc/video?url=y!cats!1    # Second result
```

### 🎲 Random Videos

```
http://s.cympfh.cc/video?url=random
```
Returns random video from curated list (updated hourly).

