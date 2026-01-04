# 🎬 s.cympfh.cc/video

VRChatプレイヤー向けのマルチメディアサービス。動画URL変換、画像ストリーミング、YouTubeサーチ機能を提供します。

## 🚀 使い方

すべての機能は同じエンドポイントを使用します：
```
http://s.cympfh.cc/video?url={URL}
```

### 📹 動画プラットフォーム

**YouTube**
```
http://s.cympfh.cc/video?url=https://www.youtube.com/watch?v=-fRA1CvuPXM
```
注意: VRChatはYouTubeを直接サポートしているため、変換は不要な場合があります。

**Bilibili**
```
http://s.cympfh.cc/video?url=https://www.bilibili.com/video/BV1sNuDzsEjM
```
VRChat互換性のためにURLをラップします。

**ニコニコ動画**
```
http://s.cympfh.cc/video?url=https://www.nicovideo.jp/watch/sm45154842
```
VRChat対応フォーマットに変換します。

**X (Twitter)**
```
http://s.cympfh.cc/video?url=https://x.com/pa_draws/status/1849228056537497835
```
VRChat再生のために動画コンテンツをプロキシします。

### 🖼️ 画像

**単一画像:**
```
http://s.cympfh.cc/video?url={IMAGE_URL}
```
画像をライブストリームに変換します。PNG、JPG、JPEG、GIF、WebP形式および画像コンテンツを返すURLをサポートします。

**画像スライドショー:**
```
http://s.cympfh.cc/video?url={IMAGE_URL_1}&url={IMAGE_URL_2}&url={IMAGE_URL_3}&interval={秒数}
```
複数の画像からスライドショーを作成します（2-10枚）。各画像は指定された秒数（デフォルト: 5秒）表示されます。スライドショーはデフォルトで10回ループします。

例:
```
http://s.cympfh.cc/video?url=https://example.com/cat1.jpg&url=https://example.com/cat2.jpg&interval=10
```

### 🔍 YouTube検索

**検索結果を画像として表示:**
```
http://s.cympfh.cc/video?url=y!cats
```

**動画に直接アクセス:**
```
http://s.cympfh.cc/video?url=y!cats!0    # 最初の結果
http://s.cympfh.cc/video?url=y!cats!1    # 2番目の結果
```

### 🎲 ランダム動画

```
http://s.cympfh.cc/video?url=random
```
厳選されたリストからランダムに動画を返します（1時間ごとに更新）。