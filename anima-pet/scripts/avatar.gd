class_name Avatar
extends RefCounted
## Layered PIPOYA character (32×32 frames, 4 directions × 3 walk frames).
## A spec only stores part ids and palette indices; textures are stacked at draw
## time and recoloured with modulate, so 60 avatars cost a few hundred draw calls.
## Parts live in assets/pipoya: PIPOYA's free pipoya.net packs, redistributable under their terms
## (see assets/pipoya/LICENSE-PIPOYA.md and tools/import_pipoya.py).

const DIR := "res://assets/pipoya/"
const FRAME := 32
enum Facing { DOWN, LEFT, RIGHT, UP }

const BODIES := ["f", "m"]
const BODY_LABELS := {"f": "女生", "m": "男生"}
const SKIN_LABELS := ["白皙", "自然", "小麦", "深色"]
## [label, hair sheet tone, multiplier]. Dark tones tint the brown sheet, light ones the blonde sheet.
const HAIR_COLORS := [
	["黑", "dark", Color(0.42, 0.42, 0.48)], ["深棕", "dark", Color(0.72, 0.62, 0.56)],
	["棕", "dark", Color(1, 1, 1)], ["栗色", "dark", Color(1.15, 0.85, 0.62)],
	["酒红", "dark", Color(1.05, 0.55, 0.55)], ["灰蓝", "dark", Color(0.7, 0.8, 1.0)],
	["浅金", "light", Color(1, 0.95, 0.85)], ["亚麻", "light", Color(0.85, 0.8, 0.72)],
	["粉", "light", Color(1, 0.72, 0.82)]]
const OUTFIT_COLORS := [
	["原色", Color(1, 1, 1)], ["藏青", Color(0.45, 0.55, 0.95)], ["天蓝", Color(0.7, 0.88, 1.15)],
	["酒红", Color(1.1, 0.55, 0.6)], ["樱粉", Color(1.15, 0.8, 0.88)], ["草绿", Color(0.75, 1.05, 0.75)],
	["薄荷", Color(0.75, 1.1, 1.0)], ["芥黄", Color(1.15, 1.0, 0.6)], ["淡紫", Color(0.85, 0.72, 1.1)],
	["炭灰", Color(0.6, 0.6, 0.65)], ["卡其", Color(1.05, 0.95, 0.75)], ["橙", Color(1.2, 0.8, 0.55)]]
const GLASSES_TINT := Color(0.35, 0.35, 0.4)

static var _manifest := {}
static var _tex := {}

static func manifest() -> Dictionary:
	if _manifest.is_empty():
		var f := FileAccess.open(DIR + "manifest.json", FileAccess.READ)
		if f == null:
			push_error("PIPOYA assets missing: run tools/import_pipoya.py")
			return {}
		_manifest = JSON.parse_string(f.get_as_text())
	return _manifest

static func ids(kind: String, body := "") -> Array:
	var m := manifest()
	var list: Array = m.get("outfits", {}).get(body, []) if kind == "outfits" else m.get(kind, [])
	var out := []
	for it in list:
		if kind == "outfits" or body.is_empty() or str(it.id).begins_with(body):
			out.append(str(it.id))
	return out

static func _item(kind: String, id: String, body := "") -> Dictionary:
	var m := manifest()
	var list: Array = m.get("outfits", {}).get(body, []) if kind == "outfits" else m.get(kind, [])
	for it in list:
		if it.id == id:
			return it
	return {}

static func outfit_label(body: String, id: String) -> String:
	return str(_item("outfits", id, body).get("label", id))

## Stable per-student default. Gender only picks the body and the pools the
## defaults are drawn from; every part stays available in the editor.
static func default_spec(seed_text: String, gender = null) -> Dictionary:
	var rng := RandomNumberGenerator.new()
	rng.seed = hash(seed_text)
	var g := str(gender).to_lower() if gender != null else ""
	var body: String = g if g in BODIES else BODIES[rng.randi() % 2]
	var hairs := ids("hair", body)
	var eyes := ids("eyes", body)
	var outfits := ids("outfits", body)
	var glasses := ""
	if rng.randf() < 0.3:
		glasses = ["g1", "g2"][rng.randi() % 2]
	return {"body": body, "skin": [0, 1, 1, 1, 2][rng.randi() % 5],
		"hair": hairs[rng.randi() % hairs.size()], "hair_color": [0, 0, 0, 1, 1, 2, 3][rng.randi() % 7],
		"eyes": eyes[rng.randi() % eyes.size()], "outfit": outfits[rng.randi() % outfits.size()],
		"outfit_color": rng.randi() % OUTFIT_COLORS.size(), "glasses": glasses}

static func random_spec(rng: RandomNumberGenerator) -> Dictionary:
	return default_spec(str(rng.randi()), BODIES[rng.randi() % 2])

## Fill gaps and drop unknown ids so stored specs survive asset updates.
static func normalize(spec, seed_text := "", gender = null) -> Dictionary:
	var d := default_spec(seed_text, gender)
	if not spec is Dictionary:
		return d
	var s: Dictionary = spec
	var body: String = s.get("body", d.body) if s.get("body") in BODIES else d.body
	if body != d.body:
		d = default_spec(seed_text, body)
	var out := {"body": body,
		"skin": clampi(int(s.get("skin", d.skin)), 0, SKIN_LABELS.size() - 1),
		"hair": s.get("hair") if ids("hair").has(s.get("hair")) else d.hair,
		"hair_color": clampi(int(s.get("hair_color", d.hair_color)), 0, HAIR_COLORS.size() - 1),
		"eyes": s.get("eyes") if ids("eyes").has(s.get("eyes")) else d.eyes,
		"outfit": s.get("outfit") if ids("outfits", body).has(s.get("outfit")) else d.outfit,
		"outfit_color": clampi(int(s.get("outfit_color", d.outfit_color)), 0, OUTFIT_COLORS.size() - 1),
		"glasses": s.get("glasses") if ids("glasses").has(s.get("glasses")) else ""}
	return out

static func texture(rel: String) -> Texture2D:
	if not _tex.has(rel):
		_tex[rel] = load(DIR + rel)
	return _tex[rel]

## Bottom-to-top layers as [Texture2D, Color].
static func layers(spec: Dictionary) -> Array:
	var m := manifest()
	if m.is_empty():
		return []
	var out := []
	var hc: Array = HAIR_COLORS[spec.hair_color]
	var hair := _item("hair", spec.hair)
	if hair.has("back"):
		out.append([texture(_tone(hair.back, hc[1])), hc[2]])
	out.append([texture(m.bodies[spec.body][spec.skin]), Color.WHITE])
	out.append([texture(_item("eyes", spec.eyes).file), Color.WHITE])
	var o := _item("outfits", spec.outfit, spec.body)
	out.append([texture(o.tint), OUTFIT_COLORS[spec.outfit_color][1]])
	if o.has("fixed"):
		out.append([texture(o.fixed), Color.WHITE])
	if not str(spec.glasses).is_empty():
		out.append([texture(_item("glasses", spec.glasses).file), GLASSES_TINT])
	if hair.has("front"):
		out.append([texture(_tone(hair.front, hc[1])), hc[2]])
	return out

static func _tone(variants: Dictionary, tone: String) -> String:
	return variants.get(tone, variants.values()[0])

static func region(facing: int, step := 1) -> Rect2:
	return Rect2(step * FRAME, facing * FRAME, FRAME, FRAME)

static func emote_texture(key: String) -> Texture2D:
	var e: Dictionary = manifest().get("emotes", {})
	return texture(e[key]) if e.has(key) else null

## The front standing frame as one image (layers multiplied by their colours), for menus and
## lists; `crop` keeps the top rows only (head and shoulders).
static func icon(spec, seed_text: String, scale := 2, crop := FRAME) -> ImageTexture:
	var out := Image.create(FRAME, FRAME, false, Image.FORMAT_RGBA8)
	for l in layers(normalize(spec, seed_text)):
		var src: Image = (l[0] as Texture2D).get_image()
		if src.is_compressed():
			src.decompress()
		var part := src.get_region(Rect2i(region(Facing.DOWN, 1)))
		part.convert(Image.FORMAT_RGBA8)
		var m: Color = l[1]
		for y in FRAME:
			for x in FRAME:
				var c := part.get_pixel(x, y)
				if c.a > 0.0:
					part.set_pixel(x, y, Color(minf(c.r * m.r, 1.0), minf(c.g * m.g, 1.0),
						minf(c.b * m.b, 1.0), c.a))
		out.blend_rect(part, Rect2i(0, 0, FRAME, FRAME), Vector2i.ZERO)
	out = out.get_region(Rect2i(0, 0, FRAME, crop))
	out.resize(FRAME * scale, crop * scale, Image.INTERPOLATE_NEAREST)
	return ImageTexture.create_from_image(out)
