# ぴぽや（PIPOYA）素材 — 再配布について

このフォルダ（`anima-pet/assets/pipoya/`）の画像は **ぴぽや https://pipoya.net/** の無料素材を加工したものです。
The images in this folder are modified from free materials by **ぴぽや (PIPOYA) — https://pipoya.net/**.

**本フォルダは Anima のコード用ライセンス（MIT）の対象外です。** ぴぽやの素材利用規約に従って、無償で再配布しています。
**This folder is NOT covered by Anima's MIT licence.** It is redistributed free of charge under PIPOYA's terms.

## 元の素材 / Sources

| 素材 | 配布ページ | 同梱 readme |
|---|---|---|
| ぴぽや32×32キャラチップ素材(キャラクターなんとかJ用) — `pipoya32x32_男女` のパーツ | https://pipoya.net/sozai/?p=742 | `readme-キャラチップ素材.txt` |
| 感情・状態アイコン追加セット１ — `pipo-emotionset.png` | https://pipoya.net/sozai/?p=665 | `readme-感情・状態アイコン.txt` |

両 readme の利用条件 / Terms stated in both readme files:

> 「営利・非営利問わずの利用OK」「加工OK」「二次配布OK（ただし素材そのものの商用販売は禁止）」「スタッフロールなどへの名前の記述・使用連絡不要」

素材利用規約（2026-10-23 時点の写し）/ Terms of use (snapshot of 2026-10-23): `terms-of-use-2026-10-23.txt`
最新版 / Current version: https://pipoya.net/sozai/terms-of-use/

## 加工内容 / Modifications

`anima-pet/tools/import_pipoya.py` によって以下の加工をしています（元データとの対応はスクリプトと `tools/pipoya_sources.json` を参照）。

- ファイル名を英数字に変更 / renamed to ASCII names
- 服を「染色用（無彩色）」と「固定色」の 2 レイヤーに分割、普段着の緑を灰色に変換 / outfits split into a neutral "tint" layer and a "fixed" colour layer; casual outfits' green converted to grey
- メガネを灰色化 / glasses converted to grey
- 感情・状態アイコンから 17 種を切り出し / 17 icons cut out of the emotion sheet

## 再配布する方へ / If you redistribute these files

ぴぽやの規約に従ってください / Follow PIPOYA's terms:

- **無償・無条件**で、**この規約（本ファイルと同梱の readme・規約）と一緒に**配布してください。
  Free of charge and without conditions, **together with these terms** (this file, the readme files and the terms snapshot).
- 元の素材が「**ぴぽや https://pipoya.net/**」のデータであることを明記してください。
  State that the originals are data by **ぴぽや https://pipoya.net/**.
- **有償での再配布・素材そのものの販売（転売）は禁止**です。素材を使ったゲームなどの作品は販売できます。
  **No paid redistribution and no sale of the materials themselves.** Works that use them (games, apps) may be sold.
