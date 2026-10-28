# GitHub Actions：构建 Windows 与 macOS 桌面包

## 仓库需要的配置

1. 仓库 **Settings → Actions → General** 中允许 GitHub Actions；若使用 Actions 白名单，允许 `actions/checkout`、`actions/upload-artifact`、`actions/download-artifact`、`astral-sh/setup-uv`。
2. 此仓库是公开仓库，工作流使用 GitHub 托管的 Windows 与 macOS runner，无需自建机器。如果组织有 Actions/runner 限制，由组织或仓库管理员放行。
3. 默认 `GITHUB_TOKEN` 保持只读即可：工作流的构建任务声明 `contents: read`，仅标签发布任务声明 `contents: write`。无需把个人 PAT、GitHub 密码或模型 API Key 存入仓库或 Actions Secrets；也不需要开启“允许创建和批准 Pull Request”。组织级强制策略仍须管理员允许。
4. 无签名包**不需要任何 Secrets**。若以后要免系统警告，需要另外申请 Windows 代码签名证书、Apple Developer ID 和公证凭据，并专门增加签名工作流；当前没有假定已有这些证书。

`eswulei` 的 collaborator 写权限可推送工作流和版本标签；不是仓库管理员时不能修改 Actions 开关或组织策略。

## 触发与产物

文件：`.github/workflows/desktop.yml`。

- 推送代码到 `main`：校验并构建三平台包。
- Pull Request：校验并构建，但无发布权限，也不使用发布 Secrets。
- **Actions → Desktop builds → Run workflow**：手动构建所选分支。
- 推送 `vMAJOR.MINOR.PATCH` 标签：版本必须与 `anima-server/pyproject.toml` 的 `project.version` 完全一致；三个平台都通过后自动创建 **Release 草稿**，附 ZIP 和 SHA-256。仓库维护者检查后点击 **Publish release** 才对外正式发布。
- 仅 Markdown 变更不自动触发桌面构建，可手动触发。

示例（不要重复创建已有标签）：

```bash
git tag v0.1.0
git push origin v0.1.0
```

Actions 产物保留 30 天。Release 草稿发布后的附件长期保留；仅有写权限的维护者能看到草稿。重跑标签工作流可更新草稿附件，但**不会覆盖已正式发布的 Release**；需修复时递增版本并创建新标签。

## 实际构建内容

- 原生 runner：`windows-latest`（x64）、`macos-15`（Apple Silicon）、`macos-15-intel`（Intel）。打包器检查 Python 的系统与架构，防止给 Intel 包误装 ARM 后端。
- Python 3.12 + `uv.lock` 锁定后端依赖；PyInstaller 6.20.0 冻结启动器和后端，用户无需开发环境。
- Godot 4.7.2 标准版（非 .NET），官方编辑器与导出模板均验证官方 SHA-512。
- 仅从 Git 跟踪文件白名单建立客户端 staging，移除开发用 Godot AI 插件/autoload，不打包 `.godot`、测试、`.env` 或用户数据库。PIPOYA manifest、字体、素材及许可包含在内。
- 启动器使用随机回环端口与随机鉴权令牌，不复用或停止源码版的 8030 服务；工作目录和持久化数据都在用户数据目录。
- 在上传前运行冻结后端的隔离健康/鉴权测试和导出客户端的无界面加载测试；Mac 验证 ad-hoc 签名。CI 不验证真实 GPU 窗口、透明度、Finder/SmartScreen、模型供应商可用性。
- 顶层 `LICENSE` / `NOTICE.md`、Godot 版权、字体/PIPOYA 许可及 Python 分发包提供的许可文件随包附带。

普通 `.github/workflows/check.yml` 保留发布安全扫描与后端测试，并改为锁定依赖。

## 本地复现

先准备对应系统的 Python 3.12 与 uv，在仓库根目录运行（只可原生构建当前系统/架构）：

```bash
uv sync --project anima-server --python 3.12 --locked --extra dev
uv run --project anima-server --locked python tools/setup_godot.py --version 4.7.2
# 使用上一步输出的 Godot 可执行文件绝对路径
uv run --project anima-server --locked --with pyinstaller==6.20.0 python tools/build_desktop.py --godot /path/to/godot --target windows-x64 --version 0.1.0
```

Mac 的 `--target` 改为 `macos-arm64` 或 `macos-x64`。首次下载导出模板约 1.3 GB，请预留带宽和磁盘空间。中间文件在 `.build/`，最终文件在 `dist/`，均已 Git 忽略。

## 常见问题

- `403` / `Resource not accessible by integration`：请管理员核查 Actions 是否开启、组织 token 策略是否允许发布写权限。
- runner 一直排队：检查组织的 Actions/托管 runner 限制及 GitHub 状态；不要把 Mac 任务改到 Windows runner。
- 版本校验失败：确保标签名称与 `project.version` 一致，更新版本后同步 `uv.lock`；不要重写已经公开的标签。
- 没有下载附件：查看失败 job 的日志。仅写入 YAML 不代表已经成功产出可执行程序。
- macOS 下载后的警告：见 [发行包使用说明](desktop-download.md)，当前不含 Developer ID 公证。
