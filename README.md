# Anima

像素风桌宠。从任意文本素材中蒸馏出一个人格，让它住在桌面上：会对话、会主动搭话，并在主人审核下随经历成长。
理论基础：EMERGI-PersonaOS（arXiv 2609.27417）——三层人格（特质 / 特征适应 / 叙事身份）、信念与状态分离、情境适应、可控演化。

| 目录 | 内容 |
|---|---|
| `anima-server/` | Python 后端（FastAPI + SQLite）：人格引擎、素材蒸馏、桌宠主动行为。见其 README |
| `anima-pet/` | Godot 4.7 客户端（原生窗口）：桌宠本体、对话、蒸馏工作台、修订审核 |

计划与进度维护在 `anima-server/docs/未修复.md`。

## 许可

代码与文档：MIT（`LICENSE`）。像素素材来自 ぴぽや（PIPOYA，https://pipoya.net/），按其条款免费再分发，**不适用 MIT，禁止收费再分发或出售素材本身**；字体为 SIL OFL 1.1。详见 `NOTICE.md`。

## 运行

### Windows 一键启动

安装 `uv` 和 Godot 4.7 后，双击根目录的 `start.bat`。脚本自动创建缺失的
`anima-server/.env`、安装锁定版本的 Python 依赖、启动后端、导入资源并打开桌宠。
首次运行需要联网下载依赖；已有 `.env` 不会被覆盖。

Godot 可放进 PATH、项目根目录或 `tools/`，也可通过 `GODOT_BIN` 环境变量或参数指定：
`-GodotPath` 同时接受可执行文件路径和包含 `Godot*.exe` 的解压目录。

```powershell
.\start.bat -GodotPath "D:\Apps\Godot\Godot.exe"
# 只运行后端（不需要 Godot）
.\start.bat -ServerOnly
```

脚本根据后端 `.env` 中的端口与 `anima` 命名空间令牌配置客户端连接；
显式设置的 `ANIMA_TOKEN` 优先。后端日志在 `anima-server/logs/`。
从桌宠菜单退出会同时停止本次启动的后端，也可在启动终端按 Ctrl+C 停止。
端口已占用时会提示错误，请先停止已有服务或修改 `.env` 中的 `PERSONA_PORT`。

### 手动启动（macOS）

```bash
cd anima-server && uv sync --extra dev && uv run anima-api
# 另一个终端
/Applications/Godot.app/Contents/MacOS/Godot --path anima-pet
```

客户端默认连接 `http://127.0.0.1:8030`，令牌 `anima-local-token`；可用环境变量 `ANIMA_API`、`ANIMA_TOKEN` 覆盖。

### 填写你自己的模型 API（必需）

Anima 不附带任何 API Key。聊天、蒸馏、演化和反思都要调用你自己的模型服务，支持任何 OpenAI 兼容接口，例如腾讯 TokenHub、DeepSeek 官方。

1. 启动后，桌宠头顶会提示「还没有填模型 API」。点一下气泡，或右键 →「设置…」→「模型服务」。
2. 填写接口地址、API Key 和模型名（服务商提供的模型 ID），先点「测试连接」，成功后点「保存」。模型名自动显示当前值，保存后立即生效，不用重启；已有 API Key 留空表示保留。首次模型名来自 `anima-server/.env` 的 `PERSONA_MODEL`（默认 `deepseek-v4-flash-0731`）。「关闭深度思考」沿用后端当前设置，初始由 `PERSONA_DISABLE_THINKING` 控制。
3. Key 只保存在本机 `anima-server/data/model.json`（权限 600，已被 git 忽略），不会再显示出来。

没填 Key 时，需要模型的操作会直接提示你去设置，不会使用假回复。Key 填错或额度不足时，会提示「模型服务拒绝了：…」。也可以不用设置页，改在 `anima-server/.env` 里填 `PERSONA_OPENAI_API_KEY`（参见 `.env.example`）。`PERSONA_MODEL_BACKEND=fake` 是只用于测试和开发的离线假模型。

## 验证

```bash
cd anima-server && uv run pytest -q && uv run ruff check .
/Applications/Godot.app/Contents/MacOS/Godot --headless --path anima-pet --script res://tests/check_scripts.gd
# 蒸馏工作台端到端（需要窗口；后端可用 PERSONA_MODEL_BACKEND=fake）：
ANIMA_API=http://127.0.0.1:8030 /Applications/Godot.app/Contents/MacOS/Godot --path anima-pet \
  --script res://tests/workbench_smoke.gd -- <截图目录> <素材.txt> <名字> [别名]
# 未配置 API 的提醒 → 在设置页填 Key → 真实回复（需要一个没有 Key、已导入该人格的后端）：
ANIMA_API=http://127.0.0.1:8032 ANIMA_PROBE_KEY=<你的 Key> /Applications/Godot.app/Contents/MacOS/Godot \
  --path anima-pet --script res://tests/model_setup_smoke.gd -- <截图目录> <persona_id> <名字>
```

调用模型时默认直连，不使用系统代理和 `*_PROXY` 环境变量。本机开着代理时，经过代理会让首 token 慢约 1.7 秒。如果你的模型服务必须通过代理才能访问，在 `anima-server/.env` 里设置 `PERSONA_MODEL_PROXY=http://127.0.0.1:端口`。
