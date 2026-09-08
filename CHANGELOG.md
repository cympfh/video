# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Fixed

- X/Twitter status URLs resolve to a `video.twimg.com` mp4 via the syndication endpoint, without nicovrc.

### Added

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
