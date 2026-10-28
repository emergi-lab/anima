class_name UI
extends RefCounted
## Palette: "town hall" look (Stardew-Valley-like) — parchment panels in brown wooden frames.
## Copied from ustc-discussion-student; Anima evolves it on its own.

const C_BG := Color("#e7d2a8")
const C_SURFACE := Color("#fcebc8")       # panels
const C_SURFACE_2 := Color("#e8c58c")     # buttons
const C_FRAME := Color("#7b4a2a")         # panel / button borders
const FRAME_W := 3
const C_TEXT := Color("#2b1a0b")          # ≥10:1 on every panel and button
const C_MUTED := Color("#5f4630")         # secondary text, ≥5:1 on panels
const C_PRIMARY := Color("#1f5578")       # links and notices, ≥4.9:1
const C_ME := Color("#9a3416")            # "my twin" highlight text, ≥5.9:1
const C_ERROR := Color("#9b1c14")
const C_INPUT := Color("#fff8e4")
const C_BTN_HOVER := Color("#f0d29e")
const C_BTN_PRESSED := Color("#d4ab6c")
const C_MINE_BG := Color("#fde3c4")
## Text on the floor sits on a dark translucent plate (draw_tag), never straight on the rug.
const C_MAP_TEXT := Color("#fff8e8")
const C_MAP_PLATE := Color(0.165, 0.10, 0.05, 0.78)
const C_MAP_OUTLINE := Color("#3a2414")
const C_MAP_GROUP := Color("#9fe0ff")
const C_MAP_ME := Color("#ffd23f")
const C_BUBBLE := Color("#fffaf0")
const C_BUBBLE_TEXT := Color("#2b1a0b")

## Text on the floor: light text on a dark translucent plate, so it reads on any rug or tile.
## pos is the baseline origin as for draw_string; align/width place it like draw_string does.
## accent (alpha > 0) draws a role-coloured bar at the plate's left edge.
## max_w > 0 shortens the text with "…" so neighbouring tags do not overlap.
static func draw_tag(ci: CanvasItem, pos: Vector2, text: String, align: HorizontalAlignment,
		width: float, fsize: int, color := C_MAP_TEXT, accent := Color(0, 0, 0, 0), max_w := 0.0) -> void:
	if text.is_empty():
		return
	var font := body_font()
	var w := font.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, fsize).x
	if max_w > 0 and w > max_w:
		while text.length() > 1 and font.get_string_size(text + "…", HORIZONTAL_ALIGNMENT_LEFT, -1, fsize).x > max_w:
			text = text.left(text.length() - 1)
		text = text.strip_edges() + "…"
		w = font.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, fsize).x
	var x := pos.x
	if width > 0 and align == HORIZONTAL_ALIGNMENT_CENTER:
		x += (width - w) / 2.0
	elif width > 0 and align == HORIZONTAL_ALIGNMENT_RIGHT:
		x += width - w
	var bar := 4.0 if accent.a > 0 else 0.0
	var ascent := font.get_ascent(fsize)
	var plate := Rect2(x - 4 - bar, pos.y - ascent - 1, w + 8 + bar, font.get_height(fsize) + 2).abs()
	plate.position = plate.position.round()
	ci.draw_rect(plate, C_MAP_PLATE)
	if bar > 0:
		ci.draw_rect(Rect2(plate.position, Vector2(bar, plate.size.y)), accent)
	ci.draw_string(font, Vector2(x, pos.y).round(), text, HORIZONTAL_ALIGNMENT_LEFT, -1, fsize, color)

## Outline floor text so it reads on any floor.
static func outline(l: Label, color := Color.BLACK, px := 4) -> Label:
	l.add_theme_color_override("font_outline_color", color)
	l.add_theme_constant_override("outline_size", px)
	return l

static var _body: Font
static var _bold: Font
static var _pixel: Font

## Noto Sans SC subset; macOS uses the bundled Fusion font for rare names.
## Some macOS system font collections cannot be read by FreeType in exported apps.
static func body_font() -> Font:
	if _body == null:
		var f: FontFile = load("res://assets/fonts/NotoSansSC-Regular.subset.woff2")
		if OS.get_name() == "macOS":
			# Duplicate: pixel_font() adds body_font() as its fallback; sharing that
			# same resource here would create a cyclic font fallback graph.
			var bundled := load("res://assets/fonts/fusion-pixel-12px-zh_hans.woff2").duplicate() as FontFile
			bundled.fallbacks = []
			bundled.allow_system_fallback = false
			f.allow_system_fallback = false
			f.fallbacks = [bundled]
		else:
			var sys := SystemFont.new()
			sys.font_names = PackedStringArray(["PingFang SC", "Microsoft YaHei UI", "Noto Sans CJK SC", "sans-serif"])
			f.fallbacks = [sys]
		_body = f
	return _body

## Bold body face for headings (pixel type is kept for the brand title only).
static func heading_font() -> Font:
	if _bold == null:
		var v := FontVariation.new()
		v.base_font = body_font()
		v.variation_embolden = 0.7
		_bold = v
	return _bold

## Fusion Pixel 12px: crisp only at multiples of 12. Used for the brand title only.
static func pixel_font() -> Font:
	if _pixel == null:
		var f: FontFile = load("res://assets/fonts/fusion-pixel-12px-zh_hans.woff2")
		f.antialiasing = TextServer.FONT_ANTIALIASING_NONE
		f.hinting = TextServer.HINTING_NONE
		f.subpixel_positioning = TextServer.SUBPIXEL_POSITIONING_DISABLED
		if OS.get_name() == "macOS":
			f.allow_system_fallback = false
		f.fallbacks = [body_font()]
		_pixel = f
	return _pixel

static func pixel_label(text: String, size := 24, color := C_TEXT) -> Label:
	var l := label(text, size, color)
	l.add_theme_font_override("font", pixel_font())
	return l

## Page and panel headings: bold body face, dark ink.
static func heading(text: String, size := 22, color := C_TEXT) -> Label:
	var l := label(text, size, color)
	l.add_theme_font_override("font", heading_font())
	return l

static func label(text: String, size := 15, color := C_TEXT) -> Label:
	var l := Label.new()
	l.text = text
	l.add_theme_font_size_override("font_size", size)
	l.add_theme_color_override("font_color", color)
	return l

## Backend timestamps are UTC ISO strings; show them in the viewer's local time.
static func local_time(iso) -> String:
	var text := str(iso)
	if text.is_empty() or text == "<null>":
		return "—"
	var unix := Time.get_unix_time_from_datetime_string(text.left(19))
	var bias: int = Time.get_time_zone_from_system().get("bias", 0)
	return Time.get_datetime_string_from_unix_time(int(unix) + bias * 60, true).left(16)
