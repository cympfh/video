"""トラックのメタデータ (タイトル・アーティスト・アートワーク) の共通型"""

from dataclasses import dataclass


@dataclass
class TrackInfo:
    """A resolved track/video's metadata."""

    url: str
    title: str
    artist: str
    artwork_url: str | None = None

    @property
    def display_title(self) -> str:
        """YamaPlayer などに表示するための1行タイトル

        >>> TrackInfo("u", "Song", "Artist").display_title
        'Artist - Song'
        >>> TrackInfo("u", "Song", "").display_title
        'Song'
        """
        if self.artist:
            return f"{self.artist} - {self.title}"
        return self.title
