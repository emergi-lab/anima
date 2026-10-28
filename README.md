# Anima

像素风桌宠：从小说、台词、聊天记录或日记中提取人物的性格证据，审阅后导入为人格，与它对话，并决定是否接受后续反思和演化。

这里的“蒸馏”是调用模型提取证据、归纳人格信念，不是训练或微调模型权重。人格引擎参考 EMERGI-PersonaOS（arXiv 2609.27417）的三层结构：叙事身份、特征适应、特质；信念、当前状态与记忆分别保存。

## 项目结构

| 路径 | 内容 |
|---|---|
| [anima-server/](anima-server/README.md) | Python 后端：FastAPI、SQLite、人格引擎、蒸馏、聊天、反思与演化 |
| [anima-pet/](anima-pet/README.md) | Godot 客户端：桌宠、聊天、蒸馏工作台、人格审阅与设置 |
| `start.sh` / `start.command` | macOS 终端 / Finder 启动入口 |
| `start.bat` / `start.ps1` | Windows 启动入口 |

实施记录与未完成事项见 [未修复.md](anima-server/docs/未修复.md)，API 见 [接口契约.md](anima-server/docs/接口契约.md)。

## 运行条件

- 安装 **uv**，并确保终端可以执行 `uv --version`。
- 安装 **Godot 4.7**；本项目使用 GDScript，不需要 .NET 版。
- 后端需要 **Python 3.11 或更高版本**；依赖由启动脚本执行 `uv sync --locked` 准备。
- 聊天、蒸馏、反思和演化需要你自己的 **OpenAI 兼容模型 API**。未配置 Key 时可以打开界面、准备素材和选择形象。

以下启动命令在项目根目录执行。首次启动需要联网下载 Python 依赖和导入 Godot 资源；已有 `anima-server/.env` 不会被覆盖。不要把 Windows 的 `.venv` 复制到 Mac 上使用。

## macOS 启动

把 Godot 放入“应用程序”后，在终端运行：

```bash
bash ./start.sh
```

脚本依次查找 `GODOT_BIN`、PATH 中的 `godot` / `godot4`、`/Applications/Godot.app`、`~/Applications/Godot.app`、项目根目录及 `tools/`。也可以指定 `.app`、可执行文件或包含 Godot 的目录，路径有空格时保留引号：

```bash
bash ./start.sh --godot-path "/Applications/Godot.app"
bash ./start.sh --godot-path "/Applications/Godot.app/Contents/MacOS/Godot"

# 只运行后端，不需要 Godot
bash ./start.sh --server-only
```

如果希望从 Finder 双击启动，先执行一次：

```bash
chmod +x start.command
```

之后双击 `start.command`，会在终端中启动项目；启动失败时保留错误信息。终端入口也接受 Windows 风格的 `-GodotPath` 和 `-ServerOnly` 参数。

## Windows 启动

双击 `start.bat`，或在 PowerShell 中执行：

```powershell
.\start.bat
.\start.bat -GodotPath "D:\Apps\Godot\Godot.exe"

# 也接受解压后的目录，包括名称以 .exe 结尾的目录
.\start.bat -GodotPath "D:\software\Godot_v4.7.2-stable_win64.exe"

# 只运行后端
.\start.bat -ServerOnly
```

Godot 可放入 PATH、项目根目录或 `tools/`；也可设置 `GODOT_BIN`。

### 启动、复用与退出

两种启动器都会准备依赖、读取后端配置、等待服务就绪，然后导入资源并打开桌宠。后端默认地址为 `http://127.0.0.1:8030`，接口页面为 `http://127.0.0.1:8030/docs`，日志保存在 `anima-server/logs/`。

- 从桌宠菜单退出或在启动终端按 Ctrl+C，会停止**本次启动器创建**的后端。
- 如果端口上已有同一项目的后端，验证进程身份、健康状态、`anima` 命名空间鉴权和 API 路由后可以复用；退出桌宠时保留这个后端。
- 只启动后端的模式遇到健康的已有后端，会检查成功后直接退出。
- 其他程序占用端口、令牌不匹配或旧后端缺少接口时会报错，不会自动杀掉占用进程。通过其他命令启动、无法确认身份的后端也不会被直接复用。
- 路由检查不能检测所有代码变动。更新后端代码后，应正常停止旧后端再启动；只重新打开桌宠不会更新后端进程。

## 第一次使用

1. **设置模型服务**：右键桌宠 →「设置…」→「模型服务」，填写接口地址、模型名和 API Key，先「测试连接」再「保存」。模型名应使用服务商实际提供的 ID；`.env.example` 中的值只是初始配置。
2. **导入素材**：打开「蒸馏工作台」，新建蒸馏，添加 UTF-8 编码的 `.txt` 或 `.md` 文件。
3. **指定人物**：填写素材中的常用名字和别名，可先「预估用量」，再开始蒸馏。预估是粗略值，不是当前模型 tokenizer 的精确计数。
4. **审阅与导入**：查看信念条目及证据原文，通过、驳回或改写后确认导入。双击桌宠即可聊天。
5. **反思与演化**：在人格窗口中查看反思、比较候选版本或添加新素材演化；需要你的确认才会改变人格。

模型设置保存后立即用于后续请求，不需要重启；API Key 留空表示保留已有值。通过设置页保存的配置优先于 `.env` 中的模型配置。`PERSONA_MODEL_BACKEND=fake` 只用于离线开发测试，会返回固定内容。

英文素材也可以导入，证据保留原文；当前蒸馏前端默认请求中文人格条目。人物候选主要识别 `名字: 台词`，小说中的姓名和别名通常需要手动填写；英文材料尚未做质量评测。

## 配置与本地数据

| 位置 / 配置 | 用途 |
|---|---|
| `anima-server/.env` | 后端端口、鉴权令牌、初始模型配置等；不存在时由启动器复制 `.env.example` |
| `PERSONA_PORT` | 后端端口，默认 `8030` |
| `PERSONA_SERVICE_TOKENS` | `token=namespace` 映射；桌宠使用 `anima` 命名空间 |
| `PERSONA_MAX_PERSONAS` | 人格数量上限，默认 `5` |
| `PERSONA_MODEL_PROXY` | 模型请求的显式代理地址；默认直连，不读取系统 / Shell 代理 |
| `anima-server/data/model.json` | 设置页保存的模型服务和 API Key，默认位置；明文存储，已被 Git 忽略 |
| `anima-server/data/anima.db` | 默认 SQLite 数据库，保存人格、聊天、记忆和蒸馏项目 |
| `user://anima.cfg` | Godot 本地设置：当前人物、形象、窗口位置、缩放、名牌和闲逛等 |

`user://` 在 macOS 对应 `~/Library/Application Support/Godot/app_userdata/Anima/`，在 Windows 对应 `%APPDATA%\Godot\app_userdata\Anima\`。

启动器会根据后端配置设置客户端的 `ANIMA_API` 和 `ANIMA_TOKEN`；显式设置的 `ANIMA_TOKEN` 优先。手动启动客户端时，两者必须与你运行的后端一致。模型 API Key 与本地后端的鉴权令牌是两种不同凭据。

**删除人物有两种范围**：默认删除人格版本、聊天与记忆，保留蒸馏素材和审阅结果；在工作台勾选删除全部信息，会同时清理该人物关联的蒸馏项目。已经删除人格的残留项目可以用「删除全部信息…」清理。删除不可恢复，但不会删除你磁盘上的原始素材文件。

## 开发与验证

后端开发命令：

```bash
cd anima-server
uv sync --locked --extra dev
uv run --locked --extra dev pytest -q
uv run --locked --extra dev ruff check .
uv run --locked --extra dev python tools/export_openapi.py

# macOS 启动器的隔离回归：不连接真实后端、不使用模型
uv run --locked --extra dev python -m unittest discover -s ../tools/tests -p 'test_start_macos.py' -v
cd ..
```

从项目根目录检查 Godot 脚本（Windows 将 Godot 路径替换为对应 `.exe`）：

```bash
"/Applications/Godot.app/Contents/MacOS/Godot" \
  --headless --path anima-pet --script res://tests/check_scripts.gd
```

需要查看客户端运行状态时，可先用启动器的 `--server-only` / `-ServerOnly` 启动后端，再用 Godot 编辑器打开 `anima-pet/project.godot`。更详细的 UI、蒸馏和聊天测试见 [客户端 README](anima-pet/README.md)。会提交人格或调用模型的测试应使用单独的测试数据库。

macOS 启动器已做隔离逻辑回归和 Bash 语法检查；当前开发环境为 Windows，macOS 上的实际启动及图形窗口仍需实机验证。

## 许可

代码与文档采用 [MIT](LICENSE)。像素素材来自 PIPOYA，字体采用 SIL OFL 1.1，适用各自许可；素材不适用项目的 MIT 许可。具体来源、再分发条件与声明见 [NOTICE.md](NOTICE.md)。
