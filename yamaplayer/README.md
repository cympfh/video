# TrackInfoLoader (YamaPlayer モジュール)

[YamaPlayer](https://github.com/koorimizuw/YamaPlayer) で
`s.cympfh.cc/video?url=...` 経由の再生にタイトルを表示するための
ドロップイン モジュールです。YamaPlayer 本体の改造は不要です。

YamaPlayer 標準の VideoInfo は YouTube / Twitch にしか
対応していませんが、このモジュールは video サーバーの
`/video/info` エンドポイントからタイトルを取得します。
SoundCloud のトラック、YouTube、ニコニコ動画、bilibili の動画に対応しています。

YamaPlayer 1.5.x（`Listener` / `Controller.AddListener` がある版）で
動作します。それより新しい版のモジュール機構（`YamaPlayerModule`）には
対応していません。

## 導入

1. `TrackInfoLoader.cs` と `YamaPlayerPatch.asmdef` を
   **同じフォルダ**に置く（例: `Assets/Gimmicks/YamaPlayerPatch/`）
   - asmdef は YamaPlayer (`Yamadev.YamaStream.Runtime`) などへの参照を宣言するものです。
     これが無いと `error CS0246: YamaPlayerModule が見つからない` になります
   - すでにそのフォルダ（または上位フォルダ）に asmdef がある場合は、
     `YamaPlayerPatch.asmdef` は置かずに、既存 asmdef の `references` に
     `Yamadev.YamaStream.Runtime` を追加してください
2. シーンに空の GameObject を作り、コンパイル後に生成される
   `TrackInfoLoader` (Udon Behaviour) をアタッチする
3. インスペクタの設定:
   - `Server Base`: video サーバーのオリジン（既定 `https://s.cympfh.cc`）
   - `Thumbnail Target`: アートワークを表示したい場合は
     Quad などの Renderer を指定（任意。空ならタイトルだけ設定する）

Controller はシーン内の YamaPlayer から自動で探します
（見つからない場合はインスペクタの `Controller` に直接指定してください）。

## 動作

- 再生 URL が `Server Base` で始まり、かつタイトル未設定のとき、
  `{Server Base}/video/info?url={再生URL}` を読み、
  返ってきたタイトルを現在のトラックとキューに設定します
- `Thumbnail Target` がある場合は
  `{Server Base}/video/thumbnail?url={再生URL}` の画像を
  `_MainTex` に設定します（変更する場合は `Thumbnail Property`）

タイトル取得には VRChat の「信頼できない URL」の許可が必要です。
