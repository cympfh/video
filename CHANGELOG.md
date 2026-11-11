# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added

- SoundCloud video takes `&effect=wave|spectrum|circle|volume` to overlay an audio-reactive waveform, frequency bars, a waveform ring around a circular artwork, or level meters (ffmpeg filters only; 15 fps, libx264 veryfast). Without `effect` nothing changes. Unknown names are 400; `effect` is ignored for other sites and with `&audio=1`.

### Added

- SoundCloud tracks play as video by default: the track artwork as a still 16:9 frame (blurred background, black without artwork) with the audio, as h264 + aac HLS encoded faster than real time. The redirect happens once the first 4-second segment exists, and the playlist carries `#EXT-X-START:TIME-OFFSET=0` so late joiners also start from the beginning. `&audio=1` keeps the old audio-only redirect.

### Added

- SoundCloud track URLs resolve to their HLS audio stream via the public api-v2 (client_id is scraped from soundcloud.com and refreshed on 401/403). Public tracks only; playlists and profiles are passed through unchanged.

### Added

- X/Twitter status URLs without a video now play as an image slideshow of the post's photos (a single photo becomes a still image stream), instead of failing with 502.

### Fixed

- iwara URLs use `https://nicovrc.net/?url=` instead of `nicovrc.net/proxy/?`.
- X/Twitter status URLs resolve to a `video.twimg.com` mp4 via the syndication endpoint, without nicovrc.

### Added

- Index page: visiting `/` or `/video` without `url` shows a form that builds `https://s.cympfh.cc/video?url=...` and copies it.

- Query parameter `p` for Bilibili multi-part videos (1-based page index). Forwarded to biliplayer as `&p=N`.
- `random-live` alias: pick a currently-live YouTube stream from a curated channel-ID gist.

## [2026-01-06]

### Changed

- ImageStream: optimize HLS `hls_list_size` and clean up stream directories.

## [2026-01-04]

### Added

- Image slideshow mode: multiple `url=` values with `interval` and `loop` parameters.

## [2025-12-17]

### Added

- `random` alias for random video selection.

## [2025-12-01]

### Added

- iwara.tv URL support via nicovrc proxy.

## [2025-09-29]

### Added

- X (Twitter) URL proxy support via nicovrc.

## [2025-09-13]

### Added

- Random video selection from a curated GitHub Gist list.
- Improved error handling.

### Changed

- Random shuffle algorithm.

## [2025-08-22]

### Added

- Image streaming via HLS (ffmpeg).
- Process limit for concurrent ImageStream jobs.
- Imgur-related handling.

## [2025-08-21]

### Added

- YouTube search (`y!{keyword}`, `y!{keyword}!{index}`).
- Image viewer / image-to-stream conversion.

## [2025-04-21]

### Added

- Initial release: FastAPI redirect API for VRChat video players.
- URL conversion for NicoNico and Bilibili.
- `/` and `/video` endpoints.
