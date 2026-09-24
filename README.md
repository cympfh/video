# 🎬 s.cympfh.cc/video

[日本語](README.ja.md)

Multi-media service for VRChat video players. Converts video URLs, streams images, and provides YouTube search functionality.

## 🚀 Usage

All features use the same endpoint:
```
https://s.cympfh.cc/video?url={URL}
```

### 📹 Video Platforms

**YouTube**
```
https://s.cympfh.cc/video?url=https://www.youtube.com/watch?v=-fRA1CvuPXM
```
Note: VRChat supports YouTube directly, so conversion may not be needed.

**Bilibili**
```
https://s.cympfh.cc/video?url=https://www.bilibili.com/video/BV16P4y1M7AR
```
Wraps URLs for VRChat compatibility.

**NicoNico**
```
https://s.cympfh.cc/video?url=https://www.nicovideo.jp/watch/sm45154842
```
Converts to VRChat-compatible format.

**X (Twitter)**
```
https://s.cympfh.cc/video?url=https://x.com/pa_draws/status/1849228056537497835
```
Proxies video content for VRChat playback.

### 🖼️ Images

**Single Image:**
```
https://s.cympfh.cc/video?url={IMAGE_URL}
```
Converts images to live streams. Supports PNG, JPG, JPEG, GIF, WebP formats and URLs returning image content.

**Image Slideshow:**
```
https://s.cympfh.cc/video?url={IMAGE_URL_1}&url={IMAGE_URL_2}&url={IMAGE_URL_3}&interval={SECONDS}
```
Creates a slideshow from multiple images (2-10 images). Each image displays for the specified interval (default: 5 seconds). The slideshow loops 10 times by default.

Example:
```
https://s.cympfh.cc/video?url=https://example.com/cat1.jpg&url=https://example.com/cat2.jpg&interval=10
```

### 🔍 YouTube Search

**Search results as image:**
```
https://s.cympfh.cc/video?url=y!cats
```

**Direct video access:**
```
https://s.cympfh.cc/video?url=y!cats!0    # First result
https://s.cympfh.cc/video?url=y!cats!1    # Second result
```

### 🎲 Random Videos

```
https://s.cympfh.cc/video?url=random
```
Returns random video from curated list (updated hourly).

### 🔴 Random Live

```
https://s.cympfh.cc/video?url=random-live
```
Returns random live broadcast from curated list (updated hourly).
