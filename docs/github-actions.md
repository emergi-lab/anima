# GitHub Actions：Windows 与 macOS 安装包

当前仅发布 **Windows x64 setup EXE、Mac Apple Silicon / Intel DMG** 和对应 SHA-256，不再生成或上传便携 ZIP。用户不需要安装 Python、uv 或 Godot。

2026-10-28 20:41（北京时间）已正式发布 [v0.0.1](https://github.com/emergi-lab/anima/releases/tag/v0.0.1)，源码 `022df2b`，[标签构建 40688551042](https://github.com/emergi-lab/anima/actions/runs/40688551042) 全部成功。59 项工具测试、Ruff、发布内容检查、三平台构建和安装验证通过；已匿名下载全部 6 个附件，核对文件大小、GitHub SHA-256 摘要及 3 对校验文件。

本次发布前远端旧标签及 Release 已不存在，因此基于当前 `main` 重新创建。提交 `df9d8d6` 已删除后端测试目录；两个工作流现在仅在存在后端测试文件时运行 pytest，恢复测试文件后自动重新启用。本次未运行已删除的后端测试。

## 仓库配置

- 公开仓库使用 GitHub 托管 `windows-latest`、`macos-15`（ARM64）、`macos-15-intel`（x64）；Ubuntu 仅做检查和发布管理，不构建 Linux 应用。
- Actions 需允许 `actions/checkout`、`actions/upload-artifact`、`actions/download-artifact`、`astral-sh/setup-uv`。
- 默认 `GITHUB_TOKEN` 只读，只有标签发布 job 请求 `contents: write`。无需个人 PAT、GitHub 密码或模型 API Key Secrets。
- Windows 商业签名、Apple Developer ID 与公证未配置。安装器格式不等于有商业签名，首次打开仍可能出现系统安全提示。

## 触发与附件

工作流：`.github/workflows/desktop.yml`。

- 推送代码到 `main`、Pull Request、手动 **Desktop builds → Run workflow**：校验并构建三平台安装包。PR 无发布权限。
- 推送 `vMAJOR.MINOR.PATCH` 标签：必须匹配 `anima-server/pyproject.toml` 的 `project.version`。三平台全部通过后创建 **Release 草稿**，校验恰好 3 个安装包 + 3 个 SHA-256；维护者确认后正式发布。
- 仅 Markdown 变更不触发桌面构建。
- 同一平台 Artifact 中只有安装包和校验文件。GitHub 下载 Artifact 时自身会加一层 ZIP，解压取出 EXE / DMG；这不是项目提供的便携 ZIP。Artifacts 保留 30 天。
- GitHub 自动生成的 **Source code (zip/tar.gz)** 是源码下载入口，无法像普通附件一样单独删除；不要将它当成安装包。

通常递增版本后创建新标签，不移动已公开标签，工作流拒绝覆盖已发布的 Release。2026-10-28 用户已删除旧 Release，并明确要求更新默认配置后仍用 `v0.0.1`：此次特例同步旧标签到新源码后重新构建。旧版“下载已发布 ZIP 再补安装器”的工作流已移除，避免带入旧默认值。

## 构建与验证

1. 独立 CPython 3.13、uv 0.11.7、锁定 `uv.lock`，执行 Ruff、后端和工具测试。
2. 下载 Godot 4.7.2 并验证官方 SHA-512；PyInstaller 6.20.0 冻结后端和启动器。拒绝 Anaconda 环境。
3. 只拷贝 Git 跟踪的运行资源，移除开发插件；不打包 `.env`、模型配置、用户数据、编辑器缓存。附带软件、素材与字体许可。
4. 导出前执行字体与空白设置页回归；冻结后端自检真实 settings API，确认新安装 URL / 模型名 / Key 均为空、`ready=false`。执行导出客户端和完整启动链 headless 自检。
5. Windows 用 NSIS 生成当前用户安装器，带快捷方式与系统卸载入口；CI 验证空格路径首次安装、重装、启动、卸载及用户数据和额外文件保留。卸载使用精确文件清单，不递归删除未知文件。
6. Mac 从 universal Godot 模板提取原生架构；App ad-hoc 签名并严格验证。生成 DMG，带 Applications 拖放入口；挂载只读镜像、模拟拷贝安装、核对 App 内容与签名，再验证安装后启动。
7. 仅上传 `.exe` / `.dmg` 与 `.sha256`，发布前再次核对附件集合与摘要。

已有用户保存的模型服务配置不会被清空；默认空白仅在无保存配置 / 无显式环境配置时生效。安装包启动器不继承开发者的 `PERSONA_*` 或 `.env`。

CI 不替代真实 GPU、透明窗口、向导 / Finder 交互、首次下载安全提示与 macOS 13–14 实机验证。

## 本地复现

准备本机架构的独立 CPython 3.13 与 uv，在仓库根目录：

```bash
uv sync --project anima-server --python 3.13 --locked --extra dev
uv run --project anima-server --locked python tools/setup_godot.py --version 4.7.2
uv run --project anima-server --locked --with pyinstaller==6.20.0 python tools/build_desktop.py --godot /path/to/godot --target windows-x64 --version 0.0.1
```

该命令只生成并验证 `.build/windows-x64/Anima-0.0.1-windows-x64` 程序目录，不生成 ZIP。安装器命令：

```bash
uv run --project anima-server --locked python tools/build_installers.py --target windows-x64 --version 0.0.1 --package .build/windows-x64/Anima-0.0.1-windows-x64
```

Windows 需要 NSIS / PowerShell 7，安装器的真实安装卸载自检仅允许在可抛弃的 GitHub runner 用户内运行，避免修改个人安装项，建议直接使用云端工作流。Mac 的目标改为 `macos-arm64` 或 `macos-x64`，使用系统 `hdiutil`。最终安装包在 `dist/`；构建目录均已忽略。

## 常见问题

- `403` / `Resource not accessible by integration`：由管理员检查组织 Actions / token 策略。
- 版本校验失败：标签须匹配项目版本；正常升级不要重写已有标签。
- 没有附件：检查失败 job，写入 YAML 不等于构建成功。
- 首次运行提示与配置、升级、数据位置：见 [安装说明](desktop-download.md)。
