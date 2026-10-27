"""Build assets/pipoya/ (in the repository) from the two free PIPOYA packs on pipoya.net,
whose bundled readme allows redistribution: 「二次配布OK（ただし素材そのものの商用販売は禁止）」.

  1. 「ぴぽや32×32キャラチップ素材(キャラクターなんとかJ用).zip」 — https://pipoya.net/sozai/?p=742
  2. 「感情・状態アイコン追加セット１.zip」 — https://pipoya.net/sozai/?p=665

Unzip both (decode the Shift-JIS names), then

    python3 tools/import_pipoya.py <…/characters/pipoya32x32_男女 dir> <感情・状態アイコン追加セット１ dir>

Every source image is checked against the pixel fingerprints in tools/pipoya_sources.json, so
the output provably comes from these pipoya.net files (the itch.io editions of the same art
forbid redistribution and must not be used). `--record` rewrites the fingerprints.

Writes ASCII-named PNGs, manifest.json and the packs' readme files. Clothing is split into a
"tint" layer (neutral pixels, recoloured in-game by multiplying a colour) and a "fixed" layer
(ribbons, ties, shoes). The casual outfits are drawn in green, so their green pixels are
converted to grey and moved to the tint layer as well. Glasses are made grey.
"""
import colorsys, hashlib, json, shutil, sys
from pathlib import Path
from PIL import Image

RECORD = "--record" in sys.argv
ARGS = [a for a in sys.argv[1:] if a != "--record"]
SRC = Path(ARGS[0])
EMO = Path(ARGS[1])
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "pipoya"
PRINTS = Path(__file__).resolve().parent / "pipoya_sources.json"
LICENSE_FILES = Path(__file__).resolve().parent / "pipoya_license"  # notice + terms snapshot

FEMALE_HAIR = 19
MALE_HAIR = 14
OUTFITS = {
    "f": [("女_服001_一般人", "便装 1", True), ("女_服002_一般人", "便装 2", True),
          ("女_服011_学生-01", "水手服", False), ("女_服011_学生-02", "水手服 · 夏", False),
          ("女_服011_学生-03", "水手服 · 领巾", False), ("女_服012_学生-01", "西装校服", False),
          ("女_服012_学生-02", "西装校服 · 夏", False), ("女_服012_学生-03", "西装校服 · 领巾", False),
          ("女_服013_スーツ-01", "西装", False), ("女_服013_スーツ-02", "西装 · 衬衫", False),
          ("女_服014_スーツ-01", "套装", False), ("女_服014_スーツ-02", "套装 · 衬衫", False)],
    "m": [("男_服001_一般人", "便装 1", True), ("男_服002_一般人", "便装 2", True),
          ("男_服013_学生-01", "立领", False), ("男_服013_学生-02", "立领 · 白", False),
          ("男_服013_学生-03", "立领 · 敞开", False), ("男_服014_学生-01", "衬衫", False),
          ("男_服014_学生-02", "衬衫 · 白", False), ("男_服014_学生-03", "衬衫 · 浅", False),
          ("男_服015_スーツ-01", "西装", False), ("男_服015_スーツ-02", "西装 · 衬衫", False),
          ("男_服016_スーツ-01", "西装 · 领带", False), ("男_服016_スーツ-02", "西装 · 领带衬衫", False)],
}
GLASSES = 7
## Emote key → icon number in pipo-emotionset.png (10 icons of 3 frames per 960×32 row; 0–89 is
## the first set, 90–179 the additional one, in the order of its readme).
EMOTES = {"talk": 2, "exclaim": 0, "question": 1, "music": 3, "star": 4, "sparkle": 5,
          "heart": 7, "blush": 9, "angry": 10, "cry": 11, "think": 13, "swirl": 15, "sleep": 16,
          "shock": 18, "sweat": 22, "idea": 23, "agree": 90}
EMOTE_SHEET = "pipo-emotionset.png"
_prints: dict[str, str] = {}
_known: dict[str, str] = {} if RECORD else json.loads(PRINTS.read_text())


def source(path: Path) -> Image.Image:
    """Open a pack image after checking it is the pipoya.net file it should be."""
    im = Image.open(path).convert("RGBA")
    key = f"{path.parent.name}/{path.name}"
    digest = hashlib.sha1(im.tobytes() + repr(im.size).encode()).hexdigest()
    if not RECORD and _known.get(key) != digest:
        sys.exit(f"{key}: not the pipoya.net image this pack expects (fingerprint mismatch)")
    _prints[key] = digest
    return im


def save(im: Image.Image, rel: str) -> str:
    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    im.save(p)
    return rel


def split_outfit(im: Image.Image, green_is_tint: bool):
    """Neutral pixels -> tint layer; saturated pixels -> fixed layer (greens -> tint for casual wear)."""
    im = im.convert("RGBA")
    tint, fixed = Image.new("RGBA", im.size), Image.new("RGBA", im.size)
    src, t, f = im.load(), tint.load(), fixed.load()
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = src[x, y]
            if not a:
                continue
            h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
            if green_is_tint and s > 0.25 and 0.17 < h < 0.45:
                v = int(min(255, (0.35 + l) * 255))  # lift: the dark green ramp becomes a light grey ramp
                t[x, y] = (v, v, v, a)
            elif s > 0.25:
                f[x, y] = (r, g, b, a)
            else:
                t[x, y] = (r, g, b, a)
    return tint, fixed


def greyscale(im: Image.Image) -> Image.Image:
    im = im.convert("RGBA")
    g = im.convert("LA").convert("RGBA")
    g.putalpha(im.getchannel("A"))
    return g


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    m = {"frame": [32, 32], "sheet": [96, 128], "rows": ["down", "left", "right", "up"],
         "bodies": {}, "eyes": [], "hair": [], "outfits": {}, "glasses": [], "emotes": {}}
    sex = {"f": "女", "m": "男"}
    for k, jp in sex.items():
        m["bodies"][k] = [save(source(SRC / "■肌" / f"{jp}_ベース001-{i:02d}.png"), f"body/{k}{i}.png")
                          for i in range(1, 5)]
        for i in range(1, 11):
            m["eyes"].append({"id": f"{k}{i:02d}", "file": save(source(SRC / "●目" / f"{jp}_目{i:03d}.png"),
                                                                 f"eyes/{k}{i:02d}.png")})
    for k, n in (("f", FEMALE_HAIR), ("m", MALE_HAIR)):
        for i in range(1, n + 1):
            item = {"id": f"{k}{i:02d}"}
            for layer, d in (("front", "★髪"), ("back", "★髪$")):
                for tone, v in (("dark", "01"), ("light", "02")):
                    p = SRC / d / f"{sex[k]}_髪{i:03d}-{v}.png"
                    if p.exists():
                        item.setdefault(layer, {})[tone] = save(source(p), f"hair/{k}{i:02d}_{layer}_{tone}.png")
            m["hair"].append(item)
    for k, items in OUTFITS.items():
        m["outfits"][k] = []
        for i, (name, label, casual) in enumerate(items, 1):
            tint, fixed = split_outfit(source(SRC / "■服" / f"{name}.png"), casual)
            o = {"id": f"{k}{i:02d}", "label": label, "tint": save(tint, f"outfit/{k}{i:02d}_tint.png")}
            if fixed.getbbox():
                o["fixed"] = save(fixed, f"outfit/{k}{i:02d}_fixed.png")
            m["outfits"][k].append(o)
    for i in range(1, GLASSES + 1):
        m["glasses"].append({"id": f"g{i}", "file": save(greyscale(source(SRC / "めがね" / f"メガネ{i:03d}-01.png")), f"glasses/g{i}.png")})
    sheet = source(EMO / EMOTE_SHEET)
    for key, n in EMOTES.items():
        x, y = n % 10 * 96, n // 10 * 32
        m["emotes"][key] = save(sheet.crop((x, y, x + 96, y + 32)), f"emote/{key}.png")
    (OUT / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=1))
    shutil.copy(SRC.parent.parent.parent / "readme.txt", OUT / "readme-キャラチップ素材.txt")
    shutil.copy(EMO / "readme.txt", OUT / "readme-感情・状態アイコン.txt")
    for f in LICENSE_FILES.iterdir():
        shutil.copy(f, OUT / f.name)
    if RECORD:
        PRINTS.write_text(json.dumps(_prints, ensure_ascii=False, indent=1, sort_keys=True))
    print("wrote", OUT, "files:", sum(1 for _ in OUT.rglob("*.png")))


if __name__ == "__main__":
    main()
