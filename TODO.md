# TODO List

## [x] パラメータを伝播できないバグ [2026-08-06 12:07 完了]

ビリビリのシリーズURLは p パラメータでページを指定することができます。

```
https://www.bilibili.com/video/BV1y13462ESA?spm_id_from=333.788.videopod.sections&vd_source=1f350c922b4746c00dc02f0e17932cec&p=4
```

これを本サーバの

```
/video?url=...
```

に渡すと、p パラメータが伝播されず、常に p=1 のページが返ってきます。
修正してください

### 修正内容

原因: 標準クエリパースが `url=` 値内の未エンコードな `&p=4` をトップレベルパラメータとして剥がしていた。

対応: API に `p` クエリパラメータを追加。ビリビリ変換時に biliplayer へ `&p=N` を付与する。

```
/video?url=https://www.bilibili.com/video/BV1y13462ESA&p=4
```
