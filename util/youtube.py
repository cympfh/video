import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

import httpx


class YouTube:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("YOUTUBE_API_KEY")
        if not self.api_key:
            raise ValueError(
                "YouTube API key is required. Set YOUTUBE_API_KEY environment variable."
            )

        self.base_url = "https://www.googleapis.com/youtube/v3"

        # キャッシュディレクトリの作成
        self.cache_dir = Path("cache")
        self.cache_dir.mkdir(exist_ok=True)

    async def search(self, keyword: str, limit: int = 6) -> List[Dict[str, str]]:
        """YouTubeでキーワードを検索して結果を返す

        Parameters
        ----------
        keyword : str
            検索キーワード
        limit : int, optional
            取得する結果の最大数 (default: 6)

        Returns
        -------
        List[Dict[str, str]]
            [{"title": str, "url": str, "thumbnail": str}, ...]
        """
        print(f"Searching YouTube for: {keyword}")
        # キャッシュキーを作成（キーワードのハッシュ）
        cache_key = hashlib.sha256(keyword.encode()).hexdigest()
        cache_file = self.cache_dir / f"yt_search_{cache_key}.json"

        # キャッシュファイルが存在し、一定時間以内の場合は読み込んで返す
        if cache_file.exists():
            file_mtime = cache_file.stat().st_mtime
            current_time = time.time()
            if current_time - file_mtime < 3600:  # 1時間以内
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached_results = json.load(f)
                    return cached_results[:limit]
            else:
                # 古いキャッシュを削除
                cache_file.unlink()

        # APIから検索結果を取得
        params = {
            "part": "snippet",
            "q": keyword,
            "type": "video",
            "maxResults": 20,
            "key": self.api_key,
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url}/search", params=params)
            response.raise_for_status()

            data = response.json()
            results = []

            for item in data.get("items", []):
                video_id = item["id"]["videoId"]
                snippet = item["snippet"]

                result = {
                    "title": snippet["title"],
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "thumbnail": snippet["thumbnails"]["medium"]["url"],
                }
                results.append(result)

            # 結果をキャッシュファイルに保存
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)

            return results[:limit]

    async def get_from_search(self, keyword: str, index: int) -> Dict[str, str]:
        """YouTube検索結果から指定したインデックスの動画を取得

        Parameters
        ----------
        keyword : str
            検索キーワード
        index : int
            取得する動画のインデックス (0-based)

        Returns
        -------
        Dict[str, str]
            {"title": str, "url": str, "thumbnail": str}

        Raises
        ------
        IndexError
            指定したインデックスが範囲外の場合
        """
        results = await self.search(keyword, limit=20)

        if index >= len(results):
            raise IndexError(
                f"Index {index} is out of range. Found {len(results)} results."
            )

        return results[index]

    async def search_result(self, keyword: str) -> str:
        """YouTube検索結果を取得

        一枚の画像にして返す

        Parameters
        ----------
        keyword : str
            検索キーワード

        Returns
        -------
        str
            検索結果の画像パス (cache/yt_search_{keyword_hash}.png)
        """
        # キャッシュキーを作成（キーワードのハッシュ）
        cache_key = hashlib.sha256(keyword.encode()).hexdigest()
        result_image = self.cache_dir / f"yt_search_{cache_key}.png"

        # キャッシュファイルが存在し、一定時間以内の場合はそのパスを返す
        if result_image.exists():
            # ファイルの作成時刻をチェック
            file_mtime = result_image.stat().st_mtime
            current_time = time.time()
            if current_time - file_mtime < 3600:  # 1時間以内
                return str(result_image)
            else:
                # 古いキャッシュを削除
                result_image.unlink()

        # YouTube検索を実行
        results = await self.search(keyword, limit=9)

        # 一時ディレクトリでサムネイル画像を準備
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            thumbnail_files = []

            # 各サムネイルをダウンロード
            async with httpx.AsyncClient() as client:
                for i, result in enumerate(results):
                    thumbnail_url = result["thumbnail"]
                    thumb_file = temp_path / f"thumb_{i}.jpg"

                    response = await client.get(thumbnail_url)
                    response.raise_for_status()

                    with open(thumb_file, "wb") as f:
                        f.write(response.content)

                    thumbnail_files.append(str(thumb_file))

            # 不足分を透明画像で埋める（9個未満の場合）
            while len(thumbnail_files) < 9:
                empty_file = temp_path / f"empty_{len(thumbnail_files)}.png"
                # 320x180の透明画像を作成
                subprocess.run(
                    ["convert", "-size", "320x180", "xc:transparent", str(empty_file)],
                    check=True,
                )
                thumbnail_files.append(str(empty_file))

            # ImageMagickで2x3グリッドに配置
            # まず各画像を320x180にリサイズし、下部に番号を追加
            numbered_files = []
            for i, thumb_file in enumerate(thumbnail_files):
                numbered_file = temp_path / f"numbered_{i}.png"

                # 画像をリサイズして番号を追加
                subprocess.run(
                    [
                        "convert",
                        thumb_file,
                        "-resize",
                        "320x180!",
                        "-gravity",
                        "south",
                        "-stroke",
                        "black",
                        "-strokewidth",
                        "2",
                        "-fill",
                        "white",
                        "-font",
                        "DejaVu-Sans-Bold",  # システムフォントを指定
                        "-pointsize",
                        "24",
                        "-annotate",
                        "+0+10",
                        str(i),
                        str(numbered_file),
                    ],
                    capture_output=True,
                )
                numbered_files.append(str(numbered_file))

            # 3x3グリッドに配置（横3列、縦3行）
            # まず各行を作成
            row1_file = temp_path / "row1.png"
            row2_file = temp_path / "row2.png"
            row3_file = temp_path / "row3.png"

            # 1行目（0, 1, 2を横に結合）
            subprocess.run(
                [
                    "convert",
                    numbered_files[0],
                    numbered_files[1],
                    numbered_files[2],
                    "+append",
                    str(row1_file),
                ],
                check=True,
            )

            # 2行目（3, 4, 5を横に結合）
            subprocess.run(
                [
                    "convert",
                    numbered_files[3],
                    numbered_files[4],
                    numbered_files[5],
                    "+append",
                    str(row2_file),
                ],
                check=True,
            )

            # 3行目（6, 7, 8を横に結合）
            subprocess.run(
                [
                    "convert",
                    numbered_files[6],
                    numbered_files[7],
                    numbered_files[8],
                    "+append",
                    str(row3_file),
                ],
                check=True,
            )

            # 3つの行を縦に結合
            subprocess.run(
                [
                    "convert",
                    str(row1_file),
                    str(row2_file),
                    str(row3_file),
                    "-append",
                    str(result_image),
                ],
                check=True,
            )

        return str(result_image)
