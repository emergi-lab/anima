class_name ChatBubble
extends Window
## Speech bubble over the pet's head. Its window is the bubble and nothing else: a see-through
## part of a sub-window can come out black on macOS, so the tail below it is drawn in the pet's
## own window (pet.gd), which is reliably transparent; this window shows only the opening of the
## tail in its bottom border.
## Lines type out a character at a time, at most LINES lines per page; a click finishes the
## page or turns to the next one. After the last page has been on screen long enough to read,
## the bubble fades. Also shows "thinking" dots while an answer is on its way, and short notes.

signal typing_changed(on: bool)
signal finished
## The size changed (a new page, the thinking dots): re-place it at the same tail tip.
signal reshaped

const WIDTH := 240.0  # widest text line, in layout units
const LINES := 3
const PAD := Vector2(14, 10)
const TAIL := 12.0
const CHAR_SECONDS := 0.045
const READ_MIN := 4.0
const READ_PER_CHAR := 0.22
const FADE := 0.4

## The App autoload, injected on creation (a global name would not resolve in test scripts).
var app: Node
var _panel: PanelContainer
var _label: Label
var _pages: PackedStringArray = []
var _page := 0
var _shown := 0.0  # characters typed on the current page
var _hold := 0.0   # seconds left before the next page or the fade
var _thinking := false
var _note := false
var _tail_x := 0.5  # where the tail meets the bubble, as a share of its width
var _fade := 0.0
var _action := Callable()  # a link: clicking the typed-out line does this

func _init() -> void:
	# Windows needs a framed, focusable native child window to present its contents
	# reliably. A correct viewport texture alone does not prove it is visible.
	var native_frame := OS.get_name() == "Windows"
	borderless = not native_frame
	transparent = not native_frame
	transparent_bg = transparent
	unfocusable = not native_frame
	minimize_disabled = true
	maximize_disabled = true
	close_requested.connect(dismiss)
	unresizable = true
	visible = false
	title = "Anima · 气泡"

func _ready() -> void:
	canvas_item_default_texture_filter = Viewport.DEFAULT_CANVAS_ITEM_TEXTURE_FILTER_NEAREST
	_panel = PanelContainer.new()
	var box := StyleBoxFlat.new()
	box.bg_color = UI.C_BUBBLE
	box.border_color = UI.C_FRAME
	box.set_border_width_all(UI.FRAME_W)
	box.set_corner_radius_all(0)  # square: round corners would leave see-through pixels
	box.content_margin_left = PAD.x
	box.content_margin_right = PAD.x
	box.content_margin_top = PAD.y
	box.content_margin_bottom = PAD.y
	_panel.add_theme_stylebox_override("panel", box)
	_panel.mouse_filter = Control.MOUSE_FILTER_STOP
	_panel.gui_input.connect(_on_click)
	add_child(_panel)
	_label = UI.label("", 16, UI.C_BUBBLE_TEXT)
	# Lines are broken by _wrap, not by the label: an autowrapping label briefly measures itself
	# narrower during layout and the bubble keeps the extra height.
	_label.add_theme_constant_override("line_spacing", 0)  # match the TextParagraph measure
	_panel.add_child(_label)
	var tail := Control.new()
	tail.mouse_filter = Control.MOUSE_FILTER_IGNORE
	tail.draw.connect(_draw_tail.bind(tail))
	add_child(tail)
	tail.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)

# ---- what to show ---------------------------------------------------------------------------

func say(text: String) -> void:
	_action = Callable()
	_thinking = false
	_note = false
	_pages = _paginate(text.strip_edges())
	_page = 0
	_start_page()

## Waiting for an answer: three dots that keep cycling until say() or note() replaces them.
func think() -> void:
	_thinking = true
	_note = false
	_pages = PackedStringArray(["·  ·  ·"])
	_page = 0
	_label.text = _pages[0]
	_label.visible_characters = 1
	_resize()
	_appear()

## A short muted message (an error, a hint); fades like a line.
func note(text: String) -> void:
	say(text)
	_note = true
	_label.add_theme_color_override("font_color", UI.C_MUTED)

## A line that does something when clicked (open the settings); underlined, and it stays a while.
func link(text: String, action: Callable) -> void:
	say(text + "点我去设置。")
	_action = action

func dismiss() -> void:
	if visible:
		_set_typing(false)
		hide()
		finished.emit()

func busy() -> bool:
	return visible and not _thinking

# ---- placement ------------------------------------------------------------------------------

## Put the tail's tip at `tip` (screen pixels), keeping the bubble inside `area`.
func place(tip: Vector2i, area: Rect2i) -> void:
	var w := size.x
	var x := clampi(tip.x - w / 2, area.position.x, area.end.x - w)
	position = Vector2i(x, maxi(area.position.y, tip.y - size.y - ceili(TAIL * content_scale_factor)))
	var tail_x := clampf(float(tip.x - x) / maxf(w, 1.0), 0.15, 0.85)
	if not is_equal_approx(tail_x, _tail_x):
		_tail_x = tail_x
		get_child(1).queue_redraw()

# ---- internals ------------------------------------------------------------------------------

func _start_page() -> void:
	_label.remove_theme_color_override("font_color")
	_label.text = _wrap(_pages[_page])
	_label.visible_characters = 0
	_shown = 0.0
	_hold = 0.0
	_resize()
	_appear()
	_set_typing(true)

func _appear() -> void:
	_fade = 0.0
	_panel.modulate.a = 1.0
	get_child(1).modulate.a = 1.0
	if not visible:
		show()
		always_on_top = false

## Break text into pages of at most LINES wrapped lines at WIDTH, preferring to end a page
## after punctuation in its second half rather than in the middle of a phrase.
func _paginate(text: String) -> PackedStringArray:
	var pages := PackedStringArray()
	var page := ""
	for ch in text:
		var next := page + ch
		if _paragraph(next, WIDTH).get_line_count() > LINES and not page.is_empty():
			var cut := _last_stop(page)
			pages.append(page.left(cut).strip_edges())
			page = page.substr(cut) + ch
		else:
			page = next
	if not page.strip_edges().is_empty():
		pages.append(page.strip_edges())
	if pages.is_empty():
		pages.append("……")
	return pages

## Index just after the last punctuation mark in the second half of a page, or its end.
func _last_stop(page: String) -> int:
	for i in range(page.length() - 1, page.length() / 2, -1):
		if page[i] in "。！？，；：、…!?,;:":
			return i + 1
	return page.length()

## The text with a line break wherever it wraps at WIDTH.
func _wrap(text: String) -> String:
	var para := _paragraph(text, WIDTH)
	var lines := PackedStringArray()
	for i in para.get_line_count():
		var r := para.get_line_range(i)
		lines.append(text.substr(r.x, r.y - r.x).strip_edges())
	return "\n".join(lines)

func _paragraph(text: String, width: float) -> TextParagraph:
	var para := TextParagraph.new()
	para.width = width
	para.break_flags = TextServer.BREAK_MANDATORY | TextServer.BREAK_WORD_BOUND | TextServer.BREAK_ADAPTIVE
	para.add_string(text, UI.body_font(), 16)
	return para

## Size the window to the current page's bubble (the tail is the pet window's).
func _resize() -> void:
	var text := _paragraph(_label.text, WIDTH).get_size().ceil() + Vector2(2, 0)
	_label.custom_minimum_size = text
	var bubble := text + PAD * 2
	bubble.x = maxf(bubble.x, 64.0)
	_label.position = (bubble - text) / 2
	_label.size = text
	_panel.size = bubble
	var f := content_scale_factor
	size = Vector2i(ceili(bubble.x * f), ceili(bubble.y * f))
	get_child(1).queue_redraw()
	reshaped.emit()

## The tail as [border, fill] polygons in bubble units from the window's top-left; the window
## clips them at its bottom edge, the pet's window draws the same polygons below it.
func tail() -> Array:
	var h := _panel.size.y
	var x := _panel.size.x * _tail_x
	var b := float(UI.FRAME_W)
	return [PackedVector2Array([Vector2(x - 9, h - b - 1), Vector2(x + 9, h - b - 1),
		Vector2(x, h + TAIL - 1)]),
		PackedVector2Array([Vector2(x - 9 + b * 1.4, h - b - 1),
		Vector2(x + 9 - b * 1.4, h - b - 1), Vector2(x, h + TAIL - b * 2.2)])]

## Opacity while it fades out.
func alpha() -> float:
	return _panel.modulate.a

func _draw_tail(c: Control) -> void:
	var t := tail()
	c.draw_colored_polygon(t[0], UI.C_FRAME)
	c.draw_colored_polygon(t[1], UI.C_BUBBLE)

func _process(delta: float) -> void:
	if not visible:
		return
	if _thinking:
		_shown += delta * 2.5
		_label.visible_characters = 1 + int(_shown) % 3 * 3
		return
	if _fade > 0.0:
		_fade -= delta
		_panel.modulate.a = clampf(_fade / FADE, 0.0, 1.0)
		get_child(1).modulate.a = _panel.modulate.a
		if _fade <= 0.0:
			dismiss()
		return
	var total := _label.text.length()
	if _label.visible_characters >= 0 and _label.visible_characters < total:
		_shown += delta / CHAR_SECONDS
		_label.visible_characters = mini(int(_shown), total)
		if _label.visible_characters >= total:
			_page_typed()
		return
	_hold -= delta
	if _hold <= 0.0:
		_next()

func _page_typed() -> void:
	_label.visible_characters = -1
	_hold = maxf(READ_MIN, _label.text.length() * READ_PER_CHAR)
	_set_typing(false)

func _next() -> void:
	if _page < _pages.size() - 1:
		_page += 1
		_start_page()
	else:
		_fade = FADE

func _on_click(event: InputEvent) -> void:
	if not (event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT):
		return
	if _thinking:
		return
	if _action.is_valid():
		var action := _action
		dismiss()
		action.call()
	elif _label.visible_characters >= 0:  # still typing: show the whole page
		_page_typed()
	else:
		_next()

func _set_typing(on: bool) -> void:
	typing_changed.emit(on)
