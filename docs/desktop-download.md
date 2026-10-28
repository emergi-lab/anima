# Anima 桌面发行包

## 下载与打开

从 [Releases](https://github.com/emergi-lab/anima/releases) 下载对应系统的 ZIP；开发构建也可从 [Actions → Desktop builds](https://github.com/emergi-lab/anima/actions/workflows/desktop.yml) 的成功运行底部 **Artifacts** 下载（需要登录 GitHub，保留 30 天；Artifacts 下载后还需解压里面的发行 ZIP）。

| 文件 | 适用系统 | 打开方式 |
|---|---|---|
| `Anima-版本-windows-x64.zip` | Windows 10/11，Intel / AMD 64 位 | 完整解压，双击 `Anima/Anima.exe` |
| `Anima-版本-macos-arm64.zip` | macOS，Apple Silicon（M 系列） | 解压，将 `Anima.app` 拖到“应用程序”，双击 |
| `Anima-版本-macos-x64.zip` | macOS，Intel | 同上 |

**不需要安装 Python、uv 或 Godot**。不要单独移动 Windows 的 EXE，也不要直接运行内部 `client` 目录：入口会自动启动私有的本地后端，桌宠退出后清理该后端。发行包不包含开发者的模型密钥、聊天、人物或数据库。

macOS 构建声明最低系统版本 13；自动化运行环境是 macOS 15，旧系统与真实桌面显示仍需要实机验证。Windows ARM 不提供原生包。

## 首次启动与安全提示

当前为无商业证书版本：Windows 未做 Authenticode 签名，macOS 仅做本地 ad-hoc 签名，**不是 Apple Developer ID 签名或公证**。

- Windows SmartScreen 可能显示“Windows 已保护你的电脑”。先核对下载来源和 SHA-256；只有确认信任此构建时，再选择“更多信息 → 仍要运行”。
- macOS 可能阻止未识别开发者的应用。确认来源后，在“系统设置 → 隐私与安全”中查看该应用的“仍要打开”。企业设备策略可能不允许绕过；不要关闭系统全局安全保护。如果出现“已损坏”或签名验证失败，请先重新下载核对校验和并反馈，不要直接取消全局 Gatekeeper。
- ZIP 同目录的 `.zip.sha256` 是校验文件。Windows 可运行 `Get-FileHash 文件.zip -Algorithm SHA256`，Mac 可运行 `shasum -a 256 文件.zip` 比对。

首次打开后，右键桌宠 →“设置…”→“模型服务”，填写自己的 OpenAI 兼容接口、模型名与 API Key。未配置时可以使用界面，但不能进行真实聊天、蒸馏和反思。

## 本地数据与升级

- Windows 后端数据：`%LOCALAPPDATA%\Anima\`。
- macOS 后端数据：`~/Library/Application Support/Anima/`。
- 后端数据库与模型配置在上述目录的 `data/` 中；日志在 `logs/` 中。模型 API Key 保存在本机配置文件，不包含在发行包中。
- Godot 窗口、形象等设置仍在 Godot 的 `user://`：Windows `%APPDATA%\Godot\app_userdata\Anima\`；Mac `~/Library/Application Support/Godot/app_userdata/Anima/`。
- 关闭旧版本后替换整个程序目录即可升级；程序目录不保存数据库。此发行包不自动迁移源码开发目录中的 `anima-server/data/`，避免误覆盖。
- 不要同时运行多个实例操作同一数据库；源码版与发行版后端数据默认独立。

启动失败请提供日志中的错误信息，分享前检查并移除私人内容，不要公开 `model.json` 或数据库。
