# GitHub Actions：构建 Windows 与 macOS 桌面包

2026-10-28 已正式发布 **[v0.0.1](https://github.com/emergi-lab/anima/releases/tag/v0.0.1)**（标签源码 `52660a9`）：[标签构建及 Release 附件上传全部成功](https://github.com/emergi-lab/anima/actions/runs/40665632353)，原有三个平台 ZIP 与 SHA-256 已公开验证；随后通过 [安装器工作流 40672445756](https://github.com/emergi-lab/anima/actions/runs/40672445756) 补充 Windows setup EXE、两个 Mac DMG 及校验文件，安装自检和公开下载均通过，原 ZIP 摘要未变。当前无需新增 Secrets 或更改仓库开关即可构建；后续标签仍先创建草稿，再由维护者确认发布。

## 仓库需要的配置

1. 仓库 **Settings → Actions → General** 中允许 GitHub Actions；若使用 Actions 白名单，允许 `actions/checkout`、`actions/upload-artifact`、`actions/download-artifact`、`actions/setup-python`、`astral-sh/setup-uv`。
2. 此仓库是公开仓库，工作流使用 GitHub 托管的 Windows 与 macOS runner，无需自建机器。如果组织有 Actions/runner 限制，由组织或仓库管理员放行。
3. 默认 `GITHUB_TOKEN` 保持只读即可：工作流的构建任务声明 `contents: read`，仅标签发布任务声明 `contents: write`。无需把个人 PAT、GitHub 密码或模型 API Key 存入仓库或 Actions Secrets；也不需要开启“允许创建和批准 Pull Request”。组织级强制策略仍须管理员允许。
4. 无签名包**不需要任何 Secrets**。若以后要免系统警告，需要另外申请 Windows 代码签名证书、Apple Developer ID 和公证凭据，并专门增加签名工作流；当前没有假定已有这些证书。

`eswulei` 的 collaborator 写权限可推送工作流和版本标签；不是仓库管理员时不能修改 Actions 开关或组织策略。

## 触发与产物

文件：`.github/workflows/desktop.yml`。

- 推送代码到 `main`：校验并构建三平台包。
- Pull Request：校验并构建，但无发布权限，也不使用发布 Secrets。
- **Actions → Desktop builds → Run workflow**：手动构建所选分支。
- 推送 `vMAJOR.MINOR.PATCH` 标签：版本必须与 `anima-server/pyproject.toml` 的 `project.version` 完全一致；三个平台都通过后自动创建 **Release 草稿**，附 Windows `-setup.exe`、Mac 两架构 `.dmg`、便携 ZIP 以及各自 SHA-256。仓库维护者检查后点击 **Publish release** 才对外正式发布。
- 仅 Markdown 变更不自动触发桌面构建，可手动触发。

示例（不要重复创建已有标签）：

```bash
git tag v0.0.1
git push origin v0.0.1
```

Actions 产物保留 30 天。Release 草稿发布后的附件长期保留；仅有写权限的维护者能看到草稿。重跑标签工作流可更新草稿附件，但**不会覆盖已正式发布的 Release**；需修复时递增版本并创建新标签。

## 为已发布的 ZIP 补充安装包

**Actions → Installers from release → Run workflow**：输入已有正式 Release 的标签；默认仅构建，勾选 `publish` 才向该 Release 追加附件。它下载并校验已发布 ZIP，只为原有二进制添加安装外壳，不移动标签、不重建程序、不覆盖已有附件。Windows 安装 / 重装 / 卸载与 Mac 镜像 / 安装后启动验证全部成功后才允许上传。已有同名且摘要一致的附件可安全跳过，不一致则拒绝覆盖。真正修改应用内容仍必须递增版本。

## 实际构建内容

- 原生 runner：`windows-latest`（x64）、`macos-15`（Apple Silicon）、`macos-15-intel`（Intel）。打包器检查 Python 的系统与架构，防止给 Intel 包误装 ARM 后端。
- Python 3.13 + `uv.lock` 锁定后端依赖；PyInstaller 6.20.0 冻结启动器和后端，用户无需开发环境。
- Godot 4.7.2 标准版（非 .NET），官方编辑器与导出模板均验证官方 SHA-512。Mac 先使用官方 universal 模板导出，再以 lipo 提取原生架构，并重新签名；最低 macOS 13。
- 仅从 Git 跟踪文件白名单建立客户端 staging，移除开发用 Godot AI 插件/autoload，不打包 `.godot`、测试、`.env` 或用户数据库。PIPOYA manifest、字体、素材及许可包含在内。
- 启动器使用随机回环端口与随机鉴权令牌，不复用或停止源码版的 8030 服务；工作目录和持久化数据都在用户数据目录。
- 在上传前运行冻结后端的隔离健康/鉴权测试、导出客户端的无界面加载测试，以及启动器实际拉起两进程的联动自检；Mac 验证 ad-hoc 签名。CI 不验证真实 GPU 窗口、透明度、Finder/SmartScreen、模型供应商可用性。
- Windows 使用 NSIS 生成免管理员的当前用户安装向导，带开始菜单 / 桌面快捷方式及系统卸载入口；卸载按编译时精确文件清单删除，保留用户数据和额外文件。CI 在临时 runner 用户中验证安装、重装、安装后后端自检、卸载、注册表和快捷方式清理。
- Mac 使用 `hdiutil` 生成 DMG，包含 App 和 Applications 快捷入口。CI 挂载只读镜像、模拟拖放到临时 Applications，逐文件核对原 App 内容、严格验签，再执行后端与完整启动链 headless 自检。DMG 不等同于 PKG 安装向导或 Apple 公证。
- 顶层 `LICENSE` / `NOTICE.md`、Godot 版权、字体/PIPOYA 许可及 Python 分发包提供的许可文件随包附带。

普通 `.github/workflows/check.yml` 保留发布安全扫描与后端测试，并改为锁定依赖。

## 本地复现

先准备对应系统的独立 CPython 3.13（uv 管理版或 python.org 安装版，不能使用 Anaconda 的虚拟环境）与 uv，在仓库根目录运行（只可原生构建当前系统/架构）：

```bash
uv sync --project anima-server --python 3.13 --locked --extra dev
uv run --project anima-server --locked python tools/setup_godot.py --version 4.7.2
# 使用上一步输出的 Godot 可执行文件绝对路径
uv run --project anima-server --locked --with pyinstaller==6.20.0 python tools/build_desktop.py --godot /path/to/godot --target windows-x64 --version 0.0.1
```

上述命令先生成便携 ZIP；安装器由 `tools/build_installers.py --target windows-x64 --version 0.0.1 --package .build/windows-x64/Anima-0.0.1-windows-x64` 生成。Windows 还需要 NSIS 和 PowerShell 7，安装 / 卸载自检仅允许在可抛弃的 GitHub Actions runner 用户内执行，避免更改个人电脑的安装项与快捷方式；建议直接用云端工作流产出。

Mac 的 `--target` 改为 `macos-arm64` 或 `macos-x64`。首次下载导出模板约 1.3 GB，请预留带宽和磁盘空间。中间文件在 `.build/`，最终文件在 `dist/`，均已 Git 忽略。

## 常见问题

- `403` / `Resource not accessible by integration`：请管理员核查 Actions 是否开启、组织 token 策略是否允许发布写权限。
- runner 一直排队：检查组织的 Actions/托管 runner 限制及 GitHub 状态；不要把 Mac 任务改到 Windows runner。
- 版本校验失败：确保标签名称与 `project.version` 一致，更新版本后同步 `uv.lock`；不要重写已经公开的标签。
- 没有下载附件：查看失败 job 的日志。仅写入 YAML 不代表已经成功产出可执行程序。
- macOS 下载后的警告：见 [发行包使用说明](desktop-download.md)，当前不含 Developer ID 公证。
