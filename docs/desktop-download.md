# Anima 桌面发行包

## 下载与打开

从 [Releases](https://github.com/emergi-lab/anima/releases) 优先下载对应系统的**安装包**：Windows 的 `-setup.exe`、Mac 的 `.dmg`。开发构建也可从 [Actions → Desktop builds](https://github.com/emergi-lab/anima/actions/workflows/desktop.yml) 的成功运行底部 **Artifacts** 下载（需要登录 GitHub，保留 30 天；GitHub 会用 ZIP 封装 Artifact，解压后取出其中的 EXE / DMG 安装包）。

| 文件 | 适用系统 | 打开方式 |
|---|---|---|
| `Anima-版本-windows-x64-setup.exe` | Windows 10/11，Intel / AMD 64 位 | 双击进入安装向导；安装后从开始菜单或桌面打开 Anima |
| `Anima-版本-macos-arm64.dmg` | macOS，Apple Silicon（M 系列） | 双击镜像，将 `Anima.app` 拖到旁边的 `Applications`，弹出镜像后从“应用程序”打开 |
| `Anima-版本-macos-x64.dmg` | macOS，Intel | 同上 |

**仅提供安装包，不再提供便携 ZIP。** GitHub 自动生成的 Source code ZIP / TAR 是源码，不是可运行安装包。DMG 是 Mac 常见的拖放式安装镜像，不是 PKG 安装向导。

**不需要安装 Python、uv 或 Godot**。不要单独移动 Windows 的 EXE，也不要直接运行内部 `client` 目录：入口会自动启动私有的本地后端，桌宠退出后清理该后端。发行包不包含开发者的模型密钥、聊天、人物或数据库。

macOS 构建声明最低系统版本 13；自动化运行环境是 macOS 15，旧系统与真实桌面显示仍需要实机验证。Windows ARM 不提供原生包。

## 首次启动与安全提示

当前为无商业证书版本：Windows 未做 Authenticode 签名，macOS 仅做本地 ad-hoc 签名，**不是 Apple Developer ID 签名或公证**。

- Windows SmartScreen 可能显示“Windows 已保护你的电脑”。先核对下载来源和 SHA-256；只有确认信任此构建时，再选择“更多信息 → 仍要运行”。
- macOS 可能阻止未识别开发者的应用。确认来源后，在“系统设置 → 隐私与安全”中查看该应用的“仍要打开”。企业设备策略可能不允许绕过；不要关闭系统全局安全保护。如果出现“已损坏”或签名验证失败，请先重新下载核对校验和并反馈，不要直接取消全局 Gatekeeper。
- 每个安装包均有同名 `.sha256` 校验文件。Windows 可运行 `Get-FileHash 文件.exe -Algorithm SHA256`，Mac 可运行 `shasum -a 256 文件.dmg` 比对。

首次打开后，右键桌宠 →“设置…”→“模型服务”，填写自己的 OpenAI 兼容接口、模型名与 API Key。**接口 URL 和模型名默认均为空**，不预选服务商；未填齐时可以使用界面，但不能进行真实聊天、蒸馏和反思。已经保存过的配置会保留，升级不会替你清空。

## 本地数据与升级

- Windows 后端数据：`%LOCALAPPDATA%\Anima\`。
- macOS 后端数据：`~/Library/Application Support/Anima/`。
- 后端数据库与模型配置在上述目录的 `data/` 中；日志在 `logs/` 中。模型 API Key 保存在本机配置文件，不包含在发行包中。
- Godot 窗口、形象等设置仍在 Godot 的 `user://`：Windows `%APPDATA%\Godot\app_userdata\Anima\`；Mac `~/Library/Application Support/Godot/app_userdata/Anima/`。
- Windows 安装器为当前用户安装到 `%LOCALAPPDATA%\Programs\Anima`，无需管理员权限；可在系统“设置 → 应用”中卸载。Mac 在“应用程序”中删除 `Anima.app` 即卸载。卸载保留上述数据库和用户设置，不自动清除个人数据。
- 升级前先退出 Anima；Windows 运行新安装器，Mac 替换“应用程序”中的整个 App。此发行包不自动迁移源码开发目录中的 `anima-server/data/`，避免误覆盖。
- 不要同时运行多个实例操作同一数据库；源码版与发行版后端数据默认独立。

启动失败请提供日志中的错误信息，分享前检查并移除私人内容，不要公开 `model.json` 或数据库。
