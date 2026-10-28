# Build from anima-server's locked uv environment. The caller supplies --distpath.
# Windows uses onedir; macOS BUNDLE separates Frameworks/code from Resources/data
# so codesign does not mistake Python .dist-info metadata for nested code bundles.
import sys
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
if sys.platform == "darwin":
    app = BUNDLE(
        coll, name="Anima.app", bundle_identifier="org.emergi.anima",
        info_plist={"LSUIElement": True, "NSHighResolutionCapable": True,
                    "LSMinimumSystemVersion": "13.0"},
    )
