# anima-pet · Anima 桌宠客户端

Godot 4.7 原生窗口（桌宠透明、无边框；所有窗口默认不置顶）。形象、主题与形象编辑器来自 `ustc/ustc-discussion-student`，此后独立演进。

## 启动

推荐在项目根目录运行 `bash ./start.sh`（macOS）或 `.\start.bat`（Windows），同时准备后端与客户端。指定 Godot 路径、Finder 双击入口与端口复用规则见 [项目 README](../README.md)。

单独开发客户端时，先启动后端，再用 Godot 编辑器打开本目录的 `project.godot`。客户端默认连接 `http://127.0.0.1:8030`，令牌为 `anima-local-token`；自定义后端时须同步设置 `ANIMA_API` 和 `ANIMA_TOKEN`。

## 桌宠

- ぴぽや（PIPOYA）分层像素形象，按 `屏幕缩放 × 界面大小` 取整数倍放大。待机会转头张望；开启"闲逛"时沿屏幕底边走动。
- 左键单击会冒出表情；拖动时显示惊叹号，松手后回到底边（闲逛关闭时停在原地）。
- 双击或右键"和某某说话…"打开输入条：回车发送，回答以气泡显示在头顶（逐字出现，三行一页，点击翻页），等待时头顶是思考表情，回答时按情绪换表情。聊天时桌宠不闲逛。
- 右键菜单：对话 / 聊天记录 / 人格 / 切换人格（含管理人格：切换、删除，最多 5 个）/ 换形象 / 蒸馏工作台 / 设置 / 退出。
- 设置：界面大小、名牌、闲逛；模型服务（任意 OpenAI 兼容接口：接口地址、你自己的 API Key、模型名，可测试连接，保存后立即生效；Key 只存本机 `anima-server/data/model.json`）。退出前会先结束当前聊天，让它马上整理成记忆。
- 人格窗口：叙事身份 / 特征适应 / 特质三页（强弱刻度条、适用情境、来源、点开看依据原文），反思页（它回顾最近的聊天写反思日记，拟出 A 微调 / B 适度 / C 明显三个演化版本，你审批采用一个或都不采用；另可"用新素材演化"），演化历史（每个版本改了什么，可回到旧版本）。只读：人格的改变都通过演化。演化跑完、确认演化时，桌宠会在头顶说一句。
- 设置保存在 `user://anima.cfg`：当前人格（导入时写入）、闲逛、名牌、界面大小，以及每个人格的形象。

## 代码结构

| 文件 | 职责 |
|---|---|
| `scripts/app.gd` | Autoload `App`：后端客户端、本地设置、羊皮纸主题、打开窗口 |
| `scripts/backend.gd` | HTTP JSON 客户端（`ANIMA_API`、`ANIMA_TOKEN`） |
| `scripts/pet.gd` | 桌宠本体：待机 / 闲逛 / 拖动 / 落下，鼠标穿透，右键菜单 |
| `scripts/avatar.gd` | PIPOYA 部件清单、默认形象（以人格 ID 为种子）、图层与配色（照搬） |
| `scripts/avatar_view.gd` | 绘制形象：走路帧、说话晃动、头顶表情、名牌 |
| `scripts/avatar_picker.gd` | 换形象窗口（照搬学生端编辑器） |
| `scripts/chat.gd` | 聊天控制：输入条与气泡的位置、`chat:say` / `chat:end`、情绪 → 表情 |
| `scripts/chat_bubble.gd` | 头顶气泡：断行分页、逐字出现、思考中、淡出；Windows 使用有边框的不透明窗口 |
| `scripts/chat_input.gd` | 输入条窗口 |
| `scripts/chat_window.gd` | 聊天窗口（输入条 ⤢ 放大）：最近聊天、正在输入、逐字出现、多行输入、收回 |
| `scripts/chat_timeline.gd` | 聊天时间线组件（聊天记录与聊天窗口共用）：气泡、内心卡片、时间标记 |
| `scripts/chat_log.gd` | 聊天记录：每段对话、"为什么这么说"、它记得的事 |
| `scripts/settings_window.gd` | 设置：桌宠选项与模型服务（自己的 API） |
| `scripts/persona_manager.gd` | 管理人格：名册、切换、删除（确认后永久删除），上限提示 |
| `scripts/persona_window.gd` | 人格窗口：三层人格、演化（预览 / 进度 / 审阅 / 确认）、演化历史与回到旧版本 |
| `scripts/workbench.gd` | 蒸馏工作台：步骤条、素材、选角色与捏形象、进度、审阅、导入 |
| `scripts/ui.gd` | 配色、字体（Noto Sans SC / Fusion Pixel）、文字小工具（照搬） |

## 素材

角色部件与头顶表情（`assets/pipoya/`）来自 ぴぽや（PIPOYA）在 pipoya.net 的免费包，包内条款允许免费再分发（「二次配布OK（ただし素材そのものの商用販売は禁止）」），因此随仓库提供。该目录不适用 MIT，附有 `LICENSE-PIPOYA.md`、两个包的原版 readme 与素材利用规约快照；再分发时须一并附上、注明来源 ぴぽや，且不得收费。

重新生成：从 https://pipoya.net/sozai/?p=742 下载《ぴぽや32×32キャラチップ素材(キャラクターなんとかJ用)》、从 https://pipoya.net/sozai/?p=665 下载《感情・状態アイコン追加セット１》，解压后运行 `python3 tools/import_pipoya.py <…/characters/pipoya32x32_男女> <感情・状態アイコン追加セット１>`。每张原图按 `tools/pipoya_sources.json` 的像素指纹核对；itch.io 上同一批美术的版本禁止再分发，不要用它。
字体在 `assets/fonts`（OFL）。

## 测试

工作台删除人物：选中已导入的人物，点击右上角“删除人物…”。默认删除人物版本、聊天和记忆，保留初始蒸馏；勾选“同时删除全部蒸馏项目、素材和审阅结果”则清除该人物所有关联蒸馏，条目从列表消失。已标记“人格已删除”的条目可点击“删除全部信息…”清理残留。此操作不可恢复，不删除磁盘原始素材文件。新接口需要重启后端及桌宠；旧后端会提示错误，不会假报成功。`Godot --path anima-pet --script res://tests/workbench_delete_smoke.gd` 使用模拟后端与独立配置验证删除交互，不删除真实数据。

Windows 窗口采用关闭 V-Sync 的显示策略，基础上限 60 帧；工作台获得焦点时上限 120 帧并关闭输入按帧合并，失焦或关闭后恢复。这样减少交互等待，同时避免桌宠独处时保持 120 帧；其他平台保持默认策略。部分设备可能出现撕裂。Godot MCP 实测绘制耗时不包含操作系统输入队列、桌面合成与屏幕扫描，不能作为完整的点击到显示延迟。

真实审阅延迟检查：通过 Godot MCP 启动项目并打开已有项目的审阅页，在 `game_eval` 执行 `return await load('res://tests/review_latency_probe.gd').new().run(get_node('/root/App')._workbench)`。脚本逐行滚动并发送鼠标按下 / 松开事件，核对选中结果，报告输入处理与绘制完成的 p50 / p95 / 最大耗时；只切换当前选择，不提交审阅或调用模型。请先保存尚未保存的条目改写。该测量不包含操作系统输入队列与显示器扫描延迟。

审阅交互回归：`Godot --path anima-pet --script res://tests/review_interaction_smoke.gd`，使用 63 条模拟条目和延迟响应验证证据缓存、快速切换、局部刷新和窗口不置顶；不连接真实后端，不改动用户配置。

名牌回归：`Godot --path anima-pet --script res://tests/name_tag_smoke.gd`，验证开关、保存、底部位置及长名缩放，桌面截图写入 `user://name-tag-*.png`。使用独立临时配置，不改动真实设置。

Windows 上聊天输入条和气泡使用带系统标题栏的原生窗口，规避无边框 / 不可聚焦子窗口显示灰块的问题；桌宠本体仍透明。此问题需要检查桌面实际画面，仅截取 Godot Viewport 纹理可能误判为正常。

运行 `Godot --path anima-pet --script res://tests/chat_render_smoke.gd` 可独立检查中文输入条与回复，不调用模型或改写用户配置；加 `-- --legacy` 对照旧窗口标志。桌面截图保存到 `user://chat-render-fixed.png` 或 `user://chat-render-legacy.png`，关闭预览主窗口结束。

测试脚本使用独立的本地配置文件，但部分集成测试会向指定后端导入或提交人格；请为这些测试启动使用临时数据库的后端。以下命令在 **anima-pet 目录**执行，Godot 路径含空格时保留引号。

```bash
G=/Applications/Godot.app/Contents/MacOS/Godot
"$G" --headless --path . --script res://tests/check_scripts.gd
# 需要窗口和运行中的后端（Fake 模型即可）：
ANIMA_API=http://127.0.0.1:8032 "$G" --path . --script res://tests/workbench_smoke.gd -- /tmp/anima-shots /path/to/material.txt 人物名字
ANIMA_API=http://127.0.0.1:8032 "$G" --path . --script res://tests/capture.gd -- /tmp/anima-shots
# 聊天：后端里已有该人格（先跑 workbench_smoke 导入一个）
ANIMA_API=http://127.0.0.1:8032 "$G" --path . --script res://tests/chat_smoke.gd -- /tmp/anima-shots test-persona 人物名字
ANIMA_API=http://127.0.0.1:8032 "$G" --path . --script res://tests/chat_window_smoke.gd -- /tmp/anima-shots test-persona 人物名字
# 反思：会真的提交一个新版本（请用数据库副本）
ANIMA_API=http://127.0.0.1:8032 "$G" --path . --script res://tests/reflection_smoke.gd -- /tmp/anima-shots test-persona 人物名字
# 人格与演化：后端里已有该人格和几段聊天；会真的提交一个新版本（请用数据库副本）
ANIMA_API=http://127.0.0.1:8032 "$G" --path . --script res://tests/persona_smoke.gd -- /tmp/anima-shots test-persona 人物名字
```
