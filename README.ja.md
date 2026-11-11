# 🎬 s.cympfh.cc/video

VRChatプレイヤー向けのマルチメディアサービス。動画URL変換、画像ストリーミング、YouTubeサーチ機能を提供します。

## 🚀 使い方

すべての機能は同じエンドポイントを使用します：
```
https://s.cympfh.cc/video?url={URL}
```

### 📹 動画プラットフォーム

**YouTube**
```
https://s.cympfh.cc/video?url=https://www.youtube.com/watch?v=-fRA1CvuPXM
```
注意: VRChatはYouTubeを直接サポートしているため、変換は不要な場合があります。

**Bilibili**
```
https://s.cympfh.cc/video?url=https://www.bilibili.com/video/BV16P4y1M7AR
```
VRChat互換性のためにURLをラップします。

**ニコニコ動画**
```
https://s.cympfh.cc/video?url=https://www.nicovideo.jp/watch/sm45154842
```
VRChat対応フォーマットに変換します。

**X (Twitter)**
```
https://s.cympfh.cc/video?url=https://x.com/pa_draws/status/1849228056537497835
```
VRChat再生のために動画コンテンツをプロキシします。動画が無く画像だけの投稿は、画像のスライドショー（1枚なら静止画）として表示します。

**SoundCloud**
```
https://s.cympfh.cc/video?url=https://soundcloud.com/forss/flickermood
```
公開トラックを映像付きで再生します。トラックのアートワークを中央に置き、16:9 の余白は同じ絵を拡大してぼかしたもので埋めた静止画に、音声を重ねます（アートワークが無いトラックは黒背景）。HLS（h264 + aac）で、最初の数秒がエンコードできた時点で再生が始まるので、VRChat のプレイヤーがタイムアウトしません。APIキーは不要です。プレイリスト・プロフィールは非対応です。

`&effect=` を付けると、アートワークの上に音に合わせて動く演出を重ねます。

| `effect` | 見た目 |
|---|---|
| （なし） | 上のとおりアートワークだけ |
| `wave` | 画面の下に白い波形 |
| `spectrum` | 画面の下に周波数のバー（左が低音、右が高音） |
| `circle` | アートワークを円く切り抜き、まわりを波形の輪が囲む |
| `volume` | 右端に左右の音量メーター |

```
https://s.cympfh.cc/video?url=https://soundcloud.com/forss/flickermood&effect=circle
```
知らない名前を渡すと 400 になります。SoundCloud 以外と、`&audio=1` のときは `effect` を無視します。演出ありは 8 fps ではなく 15 fps になり、エンコードに 2 倍ほど時間がかかりますが、再生は数秒で始まります。

従来どおり音声だけにしたいときは `&audio=1` を付けます（SoundCloud の HLS 音声ストリームへリダイレクト）。
```
https://s.cympfh.cc/video?url=https://soundcloud.com/forss/flickermood&audio=1
```

### 🖼️ 画像

**単一画像:**
```
https://s.cympfh.cc/video?url={IMAGE_URL}
```
画像をライブストリームに変換します。PNG、JPG、JPEG、GIF、WebP形式および画像コンテンツを返すURLをサポートします。

**画像スライドショー:**
```
https://s.cympfh.cc/video?url={IMAGE_URL_1}&url={IMAGE_URL_2}&url={IMAGE_URL_3}&interval={秒数}
```
複数の画像からスライドショーを作成します（2-10枚）。各画像は指定された秒数（デフォルト: 5秒）表示されます。スライドショーはデフォルトで10回ループします。

例:
```
https://s.cympfh.cc/video?url=https://example.com/cat1.jpg&url=https://example.com/cat2.jpg&interval=10
```

**アップロード:** `POST /video/upload`（multipart の `file`）で画像を保存し、同じ形のストリーム URL を `url` で返します。この URL は常に `PUBLIC_BASE_URL` の下になります。未設定時は `https://s.cympfh.cc` です。`Host` や `X-Forwarded-*` は使いません。手元のプレイヤーで再生するときは `PUBLIC_BASE_URL=http://127.0.0.1:8095` のように指定します。

### 🔍 YouTube検索

**検索結果を画像として表示:**
```
https://s.cympfh.cc/video?url=y!cats
```

**動画に直接アクセス:**
```
https://s.cympfh.cc/video?url=y!cats!0    # 最初の結果
https://s.cympfh.cc/video?url=y!cats!1    # 2番目の結果
```

### 🎲 ランダム動画

```
https://s.cympfh.cc/video?url=random
```
厳選されたリストからランダムに動画を返します（1時間ごとに更新）。

### 🔴 ランダムライブ

```
https://s.cympfh.cc/video?url=random-live
```
厳選されたリストからランダムに生放送を返します（1時間ごとに更新）。
