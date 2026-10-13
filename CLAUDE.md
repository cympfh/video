# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

FastAPI redirect service for VRChat video players. `GET /` and `GET /video` take `url` (repeatable), plus `interval` / `loop` for image slideshows and `p` for Bilibili page index.

Public examples use `https://s.cympfh.cc/video?url=...` (https, not http).

## Architecture

- **Main server**: `video-server.py`. `convert()` is async. Video / random / random-live paths `await convert()`.
- **Utility modules** in `util/` (classes are re-exported from `util/__init__.py`):
  - `sites/youtube.py` - YouTube Data API search and thumbnails. Needs `YOUTUBE_API_KEY`.
  - `streaming/image_stream.py` - Image and slideshow streaming
  - `streaming/uploads.py` - Saved image uploads and retention (newest 10, and uploaded within 7 days)
  - `random/videos.py` - Date-seeded pick from a public gist of video URLs
  - `random/live.py` - Date/hour-seeded walk of a secret gist of YouTube channel IDs or `@handle`s, first currently-live wins
  - `sites/x_video.py` - `X` class. Resolves a public status URL to a `video.twimg.com` mp4
- **Static**: `/video/stream` (HLS), `/video/uploads/{name}` (stored upload bytes)

### URL processing

`UrlType.from_url()` then `convert()`:

- **NicoNico** (`nicovideo.jp/watch/sm...`): rewrite to `https://www.nicovideo.life/watch?v={id}`.
- **Bilibili**: wrap with `https://biliplayer.91vrchat.com/player/?url=`. Optional query `p` (1-based) is appended as `&p=N`. Do not put `p` inside the raw video URL; the top-level query parser strips it.
- **iwara** (`iwara.tv/video/`): `https://nicovrc.net/?url={url}`.
- **X / Twitter** (`x.com` or `twitter.com` `/status/{id}`): `util.X.resolve_mp4()`. No nicovrc.
- **random**: gist list, shuffle seeded by UTC `YYYY/MM/DD`, index `hour % len`. Same day and hour returns the same video.
- **random-live**: same time seed, then walk the list until a channel is live. `?url=random-live` must be matched before the `random` prefix. Gist: secret `https://gist.github.com/cympfh/e8ee500adacbc1bbfc717ca7cbb2a9b4` (`random-live-users`, one `@handle` or channel ID per line). 404 if nobody is live.
- **Images**: single image, or 2+ `url=` values as a slideshow (`interval`, `loop`).
- **Uploaded images**: `POST /video/upload` (multipart field `file`) saves a real image under `uploads/` (`UPLOAD_DIR`) and returns `url` (`/video?url=...`, same shape as a remote image) plus `path` (`/video/uploads/{name}`). Names are random. Pillow work and the disk write run in a threadpool so a large upload does not block other requests. Caps: 20MB, 50 million pixels per frame, 200 frames, and 1 billion pixels across frames. Images larger than 1920×1080 are scaled down to fit that box, keeping aspect ratio: the browser does it before upload, and the server does it again so a direct API upload is stored within Full HD too. Stored files are re-encoded without EXIF/metadata; animations that pass the frame cap keep their frames. Each new upload keeps only generated files that are among the newest 10 and were uploaded within 7 days, then deletes those files and their `index.json` entries so old URLs 404. Other files in the directory are not removed. Trusted upload URLs are streamed from disk via ImageStream; they are not fetched back over HTTP. The form at `/video` accepts a drop on the existing URL box and fills that same convert/copy flow. There is no auth or rate limit on the upload route. Files live on the container filesystem: the Dockerfile has no `VOLUME`, and `make run` is `docker run --rm` without `-v`, so uploads disappear when the container is recreated. That is acceptable because this storage is temporary.
- **YouTube search**: `y!{keyword}` image grid, `y!{keyword}!{index}` direct watch URL.

## External dependencies (what actually breaks playback)

These were checked 2026-09-08 against the README samples.

- **X does not use nicovrc.** Old code sent `x.com` to `nicovrc.net/proxy/?`, which 302s to `nicovrc.net/video/?url=...mp4`. That `/video/` player page hung (0 bytes, timeout) from more than one host. The twimg mp4 itself was fine (~97s, h264 1280x720).
- **X syndication** (`https://cdn.syndication.twimg.com/tweet-result?id=&token=`): public embed JSON, not the paid API. No post, search, or auth. Returns tweet text, user, and `mediaDetails[].video_info.variants`. We pick the highest-bitrate `video/mp4`. Token is `((id / 1e15) * pi)` with zeros and the decimal point stripped. Works for public status URLs that include video. Protected accounts are not returned. `possibly_sensitive` is not specially blocked; if the JSON has an mp4 we redirect, otherwise 502. Not verified against adult tweets.
- **NicoNico is indirectly nicovrc.** We only rewrite to `nicovideo.life`. That host 302s to `https://nicovrc.net/?url={original}?site=nicovideo.life_video`, and `https://www.nicovideo.life/` itself 302s to `https://nicovrc.net`. For the README sample that nicovrc URL returned `200` `application/vnd.apple.mpegurl` immediately. That is a different path from the hanging `/video/` player used by the old X proxy.
- **iwara** calls `nicovrc.net/?url=` directly (the current nicovrc form; old `/proxy/?` still works but is the path they asked callers to leave). If nicovrc is down, iwara breaks. X no longer does.
- **Bilibili** depends on `biliplayer.91vrchat.com`, not nicovrc. README sample BV was replaced with `BV16P4y1M7AR` after the previous sample disappeared (PR #8).

## Recent changes

- PR #6 / `c3c3471`: `random-live`.
- PR #7: README `random-live` copy aligned with random; service URLs switched to https; English README links `README.ja.md`.
- PR #8: Bilibili sample URL `BV16P4y1M7AR`.
- PR #11 / `60d7def`: X status URLs resolve to twimg mp4 via syndication. Deployed on s.cympfh.cc. Confirmed `307` to `video.twimg.com`, no nicovrc hop.
- Closed PR #9: an earlier attempt that still used nicovrc `/proxy/` only to peel the Location mp4. Dropped because it still depended on nicovrc.

## Development

```bash
uv sync
uv run fastapi run video-server.py --host 0.0.0.0 --port 8080
```

Docker: `make build` then `make run` (needs `YOUTUBE_API_KEY`).

## Environment

- **YOUTUBE_API_KEY**: YouTube search and `random-live` live checks (`eventType=live` after resolving `@handle` via `forHandle`).

## Testing

`convert()` docstring examples use `asyncio.run(convert(...))` because `convert` is async. They do not cover X resolution (that hits the network).

Upload retention lives in `util/streaming/uploads.py`:

```bash
uv run python -m doctest util/streaming/uploads.py
```

Local check that matched production for X:

```
GET /video?url=https://x.com/pa_draws/status/1849228056537497835
-> 307 https://video.twimg.com/ext_tw_video/.../1280x720/....mp4
```

ffprobe: h264 1280x720, aac, duration ~96.9s, size 7173565.
