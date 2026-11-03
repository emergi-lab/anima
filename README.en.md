# Anima

[简体中文](README.md) | English

A pixel-art desktop pet that builds a character's persona from evidence in novels, dialogue, chat logs, or diaries. Review the extracted evidence, import the persona, chat with it, and decide whether to accept changes proposed through reflection and evolution.

Here, “distillation” means using a model to extract evidence and synthesize persona beliefs. It does not train or fine-tune model weights. The persona engine draws on the three-layer structure of EMERGI-PersonaOS (arXiv 2609.27417): narrative identity, characteristic adaptations, and traits. Beliefs, current state, and memories are stored separately.

## Project structure

| Path | Description |
|---|---|
| [anima-server/](anima-server/README.md) | Python backend: FastAPI, SQLite, persona engine, distillation, chat, reflection, and evolution |
| [anima-pet/](anima-pet/README.md) | Godot client: desktop pet, chat, distillation workbench, persona review, and settings |
| `start.sh` / `start.command` | macOS launchers for Terminal / Finder |
| `start.bat` / `start.ps1` | Windows launchers |

Implementation notes and outstanding work are tracked in [未修复.md](anima-server/docs/未修复.md). The API is documented in [接口契约.md](anima-server/docs/接口契约.md). These documents and the component READMEs are in Chinese.

## Download the desktop app

**[Download release v0.0.1](https://github.com/emergi-lab/anima/releases/tag/v0.0.1)**: on Windows x64, download the `-setup.exe` installer and double-click it. On Mac, download the `.dmg` for Apple Silicon or Intel, open it, and drag Anima into Applications. The app bundles Godot and the Python backend, so **no development environment is required**. Releases provide installers only, with no portable ZIP. On first launch, the API URL and model name are empty; configure your own model service to get started.

Development builds are also available under Artifacts at the bottom of successful [GitHub Actions runs](https://github.com/emergi-lab/anima/actions/workflows/desktop.yml). Published versions are listed on the [Releases page](https://github.com/emergi-lab/anima/releases).

See the [desktop distribution guide](docs/desktop-download.md) for launch instructions, security prompts, and data locations, and the [Actions setup guide](docs/github-actions.md) for workflow triggers, GitHub permissions, and publishing releases. Both guides are in Chinese. The app currently has neither Windows commercial code signing nor Apple Developer ID notarization, so your operating system may show a security prompt on first launch.

The instructions below cover **running from source**, which uses different data directories from the packaged desktop app.

## Requirements for running from source

- Install **uv** and make sure `uv --version` works in your terminal.
- Install **Godot 4.7**. This project uses GDScript; the .NET build is not required.
- The backend requires **Python 3.11 or later**. The launchers prepare dependencies with `uv sync --locked`.
- Chat, distillation, reflection, and evolution require your own **OpenAI-compatible model API**. Without an API key, you can still open the interface, prepare source material, and choose an avatar.

Run the launch commands below from the repository root. The first launch needs an internet connection to download Python dependencies and imports the Godot assets. An existing `anima-server/.env` will not be overwritten. Do not copy a Windows `.venv` to a Mac.

## Launch on macOS

Place Godot in Applications, then run:

```bash
bash ./start.sh
```

The launcher searches, in order, `GODOT_BIN`, `godot` / `godot4` on `PATH`, `/Applications/Godot.app`, `~/Applications/Godot.app`, the repository root, and `tools/`. You can also specify an `.app` bundle, an executable, or a directory containing Godot. Keep paths with spaces in quotes:

```bash
bash ./start.sh --godot-path "/Applications/Godot.app"
bash ./start.sh --godot-path "/Applications/Godot.app/Contents/MacOS/Godot"

# Run only the backend; Godot is not required
bash ./start.sh --server-only
```

To launch by double-clicking in Finder, run this once:

```bash
chmod +x start.command
```

Then double-click `start.command`. It starts the project in Terminal and keeps error messages visible if startup fails. The terminal launcher also accepts the Windows-style `-GodotPath` and `-ServerOnly` arguments.

## Launch on Windows

Double-click `start.bat`, or run it in PowerShell:

```powershell
.\start.bat
.\start.bat -GodotPath "D:\Apps\Godot\Godot.exe"

# An extracted directory is also accepted, even if its name ends in .exe
.\start.bat -GodotPath "D:\software\Godot_v4.7.2-stable_win64.exe"

# Run only the backend
.\start.bat -ServerOnly
```

You can place Godot on `PATH`, in the repository root, or in `tools/`. You can also set `GODOT_BIN`.

### Startup, backend reuse, and shutdown

Both launchers prepare dependencies, read the backend configuration, wait for the service to become ready, then import assets and open the pet. The default backend address is `http://127.0.0.1:8030`, the API documentation is at `http://127.0.0.1:8030/docs`, and logs are stored in `anima-server/logs/`.

- Quitting from the pet's menu or pressing Ctrl+C in the launch terminal stops the backend **created by that launcher invocation**.
- If a backend for the same project is already using the port, the launcher can reuse it after verifying its process identity, health, authentication for the `anima` namespace, and API routes. Quitting the pet leaves that backend running.
- In backend-only mode, the launcher checks a healthy existing backend and exits successfully.
- If another application occupies the port, tokens do not match, or an older backend is missing required routes, the launcher reports an error. It does not automatically kill the process using the port. A backend started through another command will not be reused if its identity cannot be verified.
- Route checks cannot detect every code change. After updating backend code, stop the old backend normally and restart it. Reopening the pet alone does not update the backend process.

## Getting started

The desktop interface currently uses Chinese labels; the steps below include those labels so you can find the corresponding controls.

1. **Configure the model service**: right-click the pet → Settings (`设置…`) → Model service (`模型服务`). Enter the API URL, model name, and API key. Use Test connection (`测试连接`), then Save (`保存`). Use an actual model ID offered by your provider; values in `.env.example` are only initial configuration values.
2. **Add source material**: open the Distillation workbench (`蒸馏工作台`), create a distillation project, and add UTF-8 `.txt` or `.md` files.
3. **Identify the character**: enter the character's common name and aliases as they appear in the material. You can use Estimate usage (`预估用量`) before starting distillation. This is a rough estimate, not an exact count from the selected model's tokenizer.
4. **Review and import**: inspect the belief entries and their original supporting passages. Approve, reject, or rewrite entries, then confirm the import. Double-click the pet to chat.
5. **Reflect and evolve**: use the Persona window (`人格`) to review reflections, compare candidate versions, or evolve the persona with new material. Changes to the persona require your confirmation.

Saved model settings apply immediately to subsequent requests; no restart is needed. Leaving the API key blank preserves the existing key. Configuration saved through Settings takes precedence over model settings in `.env`. `PERSONA_MODEL_BACKEND=fake` is for offline development and testing only and returns fixed content.

You can also import English source material, and evidence retains its original wording. The current distillation UI requests persona entries in Chinese by default. Character suggestions primarily recognize the `Name: dialogue` format; names and aliases in novels usually need to be entered manually. Distillation quality on English material has not yet been evaluated.

## Configuration and local data

| Location / setting | Purpose |
|---|---|
| `anima-server/.env` | Backend port, authentication tokens, initial model configuration, and other settings; copied from `.env.example` by the launcher if absent |
| `PERSONA_PORT` | Backend port; defaults to `8030` |
| `PERSONA_SERVICE_TOKENS` | `token=namespace` mappings; the pet uses the `anima` namespace |
| `PERSONA_MAX_PERSONAS` | Maximum number of personas; defaults to `5` |
| `PERSONA_MODEL_PROXY` | Explicit proxy URL for model requests; connections are direct by default and do not use system or shell proxy settings |
| `anima-server/data/model.json` | Default location for the model service configuration and API key saved through Settings; stored in plain text and ignored by Git |
| `anima-server/data/anima.db` | Default SQLite database for personas, chats, memories, and distillation projects |
| `user://anima.cfg` | Godot local settings: current persona, avatar, window position, scaling, name tag, wandering, and other preferences |

`user://` maps to `~/Library/Application Support/Godot/app_userdata/Anima/` on macOS and `%APPDATA%\Godot\app_userdata\Anima\` on Windows.

The launchers set the client's `ANIMA_API` and `ANIMA_TOKEN` from the backend configuration. An explicitly set `ANIMA_TOKEN` takes precedence. When starting the client manually, both values must match the backend you are running. The model API key and the local backend authentication token are separate credentials.

**Character deletion has two scopes**: by default, it deletes persona versions, chats, and memories while keeping distillation material and review results. Selecting the option to delete all information in the workbench also removes the character's associated distillation projects. For a persona that has already been deleted, use Delete all information (`删除全部信息…`) to remove remaining projects. Deletion cannot be undone, but it does not delete the original source files on your disk.

## Development and validation

Backend development commands:

```bash
cd anima-server
uv sync --locked --extra dev
uv run --locked --extra dev pytest -q
uv run --locked --extra dev ruff check .
uv run --locked --extra dev python tools/export_openapi.py

# Isolated macOS launcher regression tests: no real backend or model calls
uv run --locked --extra dev python -m unittest discover -s ../tools/tests -p 'test_start_macos.py' -v
cd ..
```

Check Godot scripts from the repository root. On Windows, replace the Godot path with the corresponding `.exe` path:

```bash
"/Applications/Godot.app/Contents/MacOS/Godot" \
  --headless --path anima-pet --script res://tests/check_scripts.gd
```

To inspect the running client, first start the backend with the launcher's `--server-only` / `-ServerOnly` option, then open `anima-pet/project.godot` in the Godot editor. See the [client README](anima-pet/README.md) (Chinese) for more detailed UI, distillation, and chat tests. Tests that submit personas or call a model should use a separate test database.

The macOS launcher has passed isolated logic regression tests and Bash syntax checks. Development currently takes place on Windows; actual startup and graphical windows on macOS still need verification on a Mac.

## License

Code and documentation are licensed under [MIT](LICENSE). Pixel assets come from PIPOYA, and fonts use SIL OFL 1.1; each is covered by its own license. The project's MIT license does not apply to those assets. See [NOTICE.md](NOTICE.md) for sources, redistribution conditions, and notices.
