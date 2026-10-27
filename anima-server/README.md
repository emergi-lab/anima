# anima-server · Anima 后端

Anima 桌宠的后端，FastAPI + SQLite，只监听本机。人格引擎 `persona_os` 按论文 EMERGI-PersonaOS（arXiv 2609.27417）实现人格对象的全生命周期：
三层信念（叙事身份 / 特征适应 / 特质）与版本、当前人格状态、记忆、状态监测、反思与受控演化。

- 起点是 `ustc/ustc-persona`（2026-10-22 拷贝），此后独立演进；其历史记录见原项目文档。
- 每次行动固定 2 次模型调用：状态推断；生成 + 复核。
- 新增中：素材蒸馏（任意文本 → 初始信念 B0，可逐条审阅证据）、桌宠主动搭话。

```bash
cp .env.example .env
uv sync --extra dev
uv run anima-api          # http://127.0.0.1:8030/docs
uv run pytest -q && uv run ruff check .
uv run python -m tools.export_openapi   # 接口变化后重新导出 docs/openapi.json
```

| 文档 | 内容 |
|---|---|
| `docs/接口契约.md` | 对外接口 v1 |
| `docs/未修复.md` | 计划与实施记录 |
| `docs/编写代码规则.md` | 编码与验证规则 |
