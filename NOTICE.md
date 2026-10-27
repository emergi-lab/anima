# 第三方素材与软件 / Third-party assets and software

本仓库的代码与文档按 `LICENSE`（MIT）发布。下列内容**不适用 MIT**，按各自的条款使用和再分发。
The code and docs are MIT (`LICENSE`). The items below are **not** MIT and keep their own terms.

| 内容 | 位置 | 作者 / 来源 | 条款 |
|---|---|---|---|
| 像素角色部件、头顶表情 | `anima-pet/assets/pipoya/` | ぴぽや（PIPOYA） https://pipoya.net/ | ぴぽや素材利用规约：可商用、可修改、可**免费**再分发（须附条款并注明来源 ぴぽや）；**禁止收费再分发或出售素材本身**。详见该目录 `LICENSE-PIPOYA.md` |
| Fusion Pixel Font | `anima-pet/assets/fonts/fusion-pixel-*` | TakWolf https://github.com/TakWolf/fusion-pixel-font | SIL OFL 1.1（`FusionPixel-OFL.txt`） |
| Noto Sans SC（子集） | `anima-pet/assets/fonts/NotoSansSC-*` | Google / Adobe | SIL OFL 1.1（`NotoSansSC-OFL.txt`） |
| Godot AI 编辑器插件（仅开发用） | `anima-pet/addons/godot_ai/` | Godot AI contributors | MIT（`addons/godot_ai/LICENSE`） |
| Godot Engine（运行时，不在仓库内） | — | Godot Engine contributors https://godotengine.org | MIT；分发导出的程序时须附其版权声明 |

PIPOYA 素材由 `anima-pet/tools/import_pipoya.py` 从 pipoya.net 的免费包生成，每张原图都按 `tools/pipoya_sources.json` 的像素指纹核对。
**同一批美术在 itch.io 上的版本禁止再分发，请勿用它替换本目录。**
The PIPOYA files are generated from pipoya.net's free packs and checked against pixel fingerprints.
**The itch.io editions of the same art forbid redistribution — do not use them here.**
