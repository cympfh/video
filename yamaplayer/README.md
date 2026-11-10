# TrackInfoLoader (YamaPlayer モジュール)

[YamaPlayer](https://github.com/koorimizuw/YamaPlayer) で
`s.cympfh.cc/video?url=...` 経由の再生にタイトルを表示するための
ドロップイン モジュールです。YamaPlayer 本体の改造は不要です。

YamaPlayer 標準の VideoInfoDownloader は YouTube / Twitch にしか
対応していませんが、このモジュールは video サーバーの
`/video/info` エンドポイントからタイトルを取得します。
いまのところ SoundCloud のトラックに対応しています。

## 導入

1. `TrackInfoLoader.cs` をワールドプロジェクトの `Assets/` 以下に置く
   （UdonSharp がコンパイルできる場所ならどこでもよい）
2. シーンに空の GameObject を作り、コンパイル後に生成される
   `TrackInfoLoader` (Udon Behaviour) をアタッチする
3. インスペクタの設定:
   - `Server Base`: video サーバーのオリジン（既定 `https://s.cympfh.cc`）
   - `Thumbnail Target`: アートワークを表示したい場合は
     Quad などの Renderer を指定（任意。空ならタイトルだけ設定する）

Controller はシーン内の YamaPlayer から自動で探します。

## 動作

- 再生 URL が `Server Base` で始まり、かつタイトル未設定のとき、
  `{Server Base}/video/info?url={再生URL}` を読み、
  返ってきたタイトルを現在のトラックとキューに設定します
- `Thumbnail Target` がある場合は
  `{Server Base}/video/thumbnail?url={再生URL}` の画像を
  `_MainTex` に設定します（変更する場合は `Thumbnail Property`）

タイトル取得には VRChat の「信頼できない URL」の許可が必要です。
