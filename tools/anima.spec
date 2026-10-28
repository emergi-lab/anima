# Build from anima-server's locked uv environment. The caller supplies --distpath.
# Produces Anima/Anima[.exe] + Anima/_internal (onedir), NOT a macOS BUNDLE.
# macOS packaging must preserve _internal beside Contents/MacOS/Anima.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).parent
source = root / "anima-server" / "src"

# Include only package-owned resources: prompts, schemas and construct registries.
# Never collect the repository root, .env, data/, logs/, or developer configuration.
datas = collect_data_files("persona_os", includes=["**/*.json", "**/*.md"])
hiddenimports = (
    collect_submodules("persona_os")
    + collect_submodules("uvicorn")
    + ["sqlalchemy.dialects.sqlite.aiosqlite", "sqlalchemy.dialects.sqlite.pysqlite",
       "aiosqlite", "sqlite3", "pydantic_core", "pydantic_settings"]
)

a = Analysis(
    [str(root / "tools" / "desktop_launcher.py")],
    pathex=[str(source)],
    binaries=[], datas=datas, hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["tkinter", "pytest"], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="Anima",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=True, contents_directory="_internal",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Anima")
