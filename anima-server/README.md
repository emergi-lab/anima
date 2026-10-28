# anima-server · Anima 后端

FastAPI + SQLite 本地服务，提供人格蒸馏、聊天、记忆、反思和受审阅控制的演化。通用人格引擎 `persona_os` 管理三层信念、版本与当前状态；桌宠聊天和素材蒸馏分别位于 `chat/`、`distill/`。

初始代码来自 `ustc/ustc-persona`，此后独立维护。素材蒸馏已经实现；当前不提供独立的定时主动搭话服务。

## 启动

推荐从项目根目录使用启动器，详见 [项目 README](../README.md)：

```bash
# macOS，只运行后端
bash ./start.sh --server-only
```

Windows 对应 `.\start.bat -ServerOnly`。启动器会保留已有 `.env`、安装锁定依赖，并处理端口和已有服务。

如需手动运行，以下命令在 **anima-server 目录**执行：

```bash
# 仅在 .env 不存在时创建；不要覆盖自己的配置
test -f .env || cp .env.example .env
uv sync --locked
uv run --locked python -m persona_os.main
# 开发时自动重载：uv run --locked python -m persona_os.main --reload
```

默认接口页为 `http://127.0.0.1:8030/docs`。运行入口也提供 `anima-api` 命令。自动重载适用于开发；启动器不会接管无法确认身份的运行进程。

## 模型与数据

- 模型配置来自 `.env`，桌宠设置页保存到 `data/model.json` 的配置优先。支持 OpenAI 兼容接口，模型名使用服务商实际提供的 ID。
- 不附带 API Key。没有 Key 时拒绝需要模型的操作；`PERSONA_MODEL_BACKEND=fake` 只用于开发与测试。
- 通用执行默认使用两次模型调用（状态推断；生成与复核）；桌宠聊天使用合并的 `respond` 调用。聊天结束后的记忆整理是额外的后台调用。
- 默认数据库是 `data/anima.db`。`.env`、模型凭据、数据库和日志均应留在本机，不提交到 Git。
- 普通删除人格保留初始蒸馏项目；显式 `/v1/personas/{id}/all-data` 删除该人物全部关联数据。运行中的相关任务会阻止删除，具体范围见接口契约。

## 测试与接口导出

仍在 anima-server 目录执行：

```bash
uv sync --locked --extra dev
uv run --locked --extra dev pytest -q
uv run --locked --extra dev ruff check .
uv run --locked --extra dev python tools/export_openapi.py
```

UI 集成测试若使用 `PERSONA_MODEL_BACKEND=fake`，应同时指定独立的 `PERSONA_DATABASE_URL` 和 `PERSONA_MODEL_FILE`，避免真实数据或已保存的模型配置进入测试。

| 文档 | 内容 |
|---|---|
| [接口契约](docs/接口契约.md) | 对外接口及字段语义 |
| [OpenAPI](docs/openapi.json) | 自动导出的接口定义 |
| [未修复](docs/未修复.md) | 计划、限制与实施记录 |
| [编写代码规则](docs/编写代码规则.md) | 编码与验证要求 |
