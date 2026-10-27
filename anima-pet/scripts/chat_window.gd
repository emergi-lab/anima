class_name ChatWindow
extends Window
## The chat as a window, like a messenger's chat page: recent lines above (ChatTimeline, a time
## only after a 5-minute gap), a typing bubble while the persona answers, its reply typed out,
## and a multi-line box below (Enter sends, Shift+Enter breaks the line). Opened from the input
## bar's ⤢; ⤡ goes back to the input bar with whatever is typed. It remembers where it was.

signal sent(text: String)
signal collapsed(text: String)

const BASE_SIZE := Vector2i(480, 720)
const MIN_SIZE := Vector2i(380, 480)
const PAGE := 5  # chats loaded at a time

## The App autoload, injected on creation (a global name would not resolve in test scripts).
var app: Node
var _icon: TextureRect
var _name: Label
var _state: Label
var _timeline: ChatTimeline
var _box: TextEdit
var _send: Button
var _limit := PAGE
var _waiting := false

func _init() -> void:
	title = "Anima · 对话"
	size = BASE_SIZE
	min_size = MIN_SIZE
	wrap_controls = false
	visible = false
	close_requested.connect(_remember_and_hide)

func _ready() -> void:
	var bg := Panel.new()
	bg.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var sb := StyleBoxFlat.new()
	sb.bg_color = UI.C_BG
	bg.add_theme_stylebox_override("panel", sb)
	add_child(bg)
	var root := VBoxContainer.new()
	root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	root.add_theme_constant_override("separation", 0)
	add_child(root)

	var head := PanelContainer.new()
	head.add_theme_stylebox_override("panel", _strip(UI.C_SURFACE, 0, 3))
	root.add_child(head)
	var top := HBoxContainer.new()
	top.add_theme_constant_override("separation", 8)
	head.add_child(top)
	_icon = TextureRect.new()
	_icon.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
	_icon.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	_icon.custom_minimum_size = Vector2(56, 44)
	top.add_child(_icon)
	var who := VBoxContainer.new()
	who.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	who.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	who.add_theme_constant_override("separation", 0)
	top.add_child(who)
	_name = UI.heading("", 18)
	who.add_child(_name)
	_state = UI.label("", 12, UI.C_MUTED)
	who.add_child(_state)
	var back := _tool("⤡", "收回成小输入条", func() -> void: collapsed.emit(_take_text()))
	back.add_theme_font_size_override("font_size", 24)  # the arrow glyph is small otherwise
	top.add_child(back)
	top.add_child(_tool("记录", "完整的聊天记录", func() -> void: app.open_chat_log(self)))
	top.add_child(_tool("✕", "关闭", _remember_and_hide))

	var paper := PanelContainer.new()
	paper.size_flags_vertical = Control.SIZE_EXPAND_FILL
	paper.add_theme_stylebox_override("panel", _strip(UI.C_INPUT, 0, 0, 10))
	root.add_child(paper)
	_timeline = ChatTimeline.new()
	_timeline.app = app
	_timeline.bubble_width = 250.0
	_timeline.icon_only = true
	_timeline.more_requested.connect(func() -> void:
		_limit += PAGE
		reload())
	paper.add_child(_timeline)

	var foot := PanelContainer.new()
	foot.add_theme_stylebox_override("panel", _strip(UI.C_SURFACE, 3, 0))
	root.add_child(foot)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	foot.add_child(row)
	_box = TextEdit.new()
	_box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_box.custom_minimum_size.y = 72
	_box.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	_box.gui_input.connect(_on_box_input)
	row.add_child(_box)
	_send = Button.new()
	_send.text = "发送"
	_send.size_flags_vertical = Control.SIZE_SHRINK_END
	_send.pressed.connect(_submit)
	row.add_child(_send)
	app.settings_changed.connect(func() -> void:
		if visible:
			content_scale_factor = app.ui_factor())

func _tool(text: String, tip: String, pressed: Callable) -> Button:
	var b := Button.new()
	b.text = text
	b.flat = true
	b.tooltip_text = tip
	b.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	b.pressed.connect(pressed)
	return b

func _strip(color: Color, top: int, bottom: int, pad := 10) -> StyleBoxFlat:
	var b := StyleBoxFlat.new()
	b.bg_color = color
	b.border_color = Color(UI.C_FRAME, 0.5)
	b.border_width_top = top
	b.border_width_bottom = bottom
	b.set_content_margin_all(pad)
	return b

# ---- showing --------------------------------------------------------------------------------

## Show the window (where it was last time, else centred) with `text` in the box. Already open
## (perhaps under the chat log or another window): bring it to the front instead.
func open_with(text: String) -> void:
	if visible:
		if not text.is_empty():
			restore(text)
		bring_up()
		return
	var saved: Variant = app.window_rect("chat_window")
	if saved == null:
		app.show_window(self, BASE_SIZE, MIN_SIZE)
	else:
		content_scale_factor = app.ui_factor()
		min_size = Vector2i(Vector2(MIN_SIZE) * content_scale_factor)
		var fit: Rect2i = app.fit_on_screen(saved)
		position = fit.position
		size = fit.size
		show()
		app.bring_forward(self)
		await get_tree().process_frame
		var placed: Rect2i = app.fit_on_screen(Rect2i(position, size))
		if placed != Rect2i(position, size):  # the system moved it: back into view
			position = placed.position
			size = placed.size
	_box.text = text
	_box.set_caret_column(text.length())
	_box.grab_focus()
	await reload()

func bring_up() -> void:
	# Window.move_to_foreground() and grab_focus() only move the keyboard focus on macOS; the
	# DisplayServer call orders the window to the front (and activates the app).
	DisplayServer.window_move_to_foreground(get_window_id())
	_box.grab_focus()

func _remember_and_hide() -> void:
	if visible:
		app.save_window_rect("chat_window", Rect2i(position, size))
		hide()

func _take_text() -> String:
	var text := _box.text
	_box.text = ""
	_remember_and_hide()
	return text

func reload() -> void:
	_icon.texture = app.persona_icon(app.persona_id, 2)
	_name.text = app.persona_name
	set_waiting(_waiting)
	if app.persona_id.is_empty():
		_timeline.clear()
		return
	var r: Dictionary = await app.api.request_json("/v1/personas/%s/chats?limit=%d" % [
		app.persona_id.uri_encode(), _limit])
	if not r.ok:
		_state.text = str(r.error)
		return
	_timeline.clear(r.data.size() >= _limit)
	for i in range(r.data.size() - 1, -1, -1):  # oldest first
		for t in r.data[i].turns:
			_timeline.add_gap_mark(t.at)
			_timeline.add_turn(t, false)
	if _waiting:
		_timeline.show_typing()
	_timeline.scroll_to_end()

# ---- talking --------------------------------------------------------------------------------

func _on_box_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo \
			and event.keycode in [KEY_ENTER, KEY_KP_ENTER] and not event.shift_pressed:
		_box.accept_event()
		_submit()

func _submit() -> void:
	var text := _box.text.strip_edges()
	if text.is_empty() or _waiting:
		return
	_box.text = ""
	sent.emit(text)

## The owner's line went out: show it, then the typing bubble until the reply comes.
func add_owner(text: String) -> void:
	var t := {"speaker": "owner", "text": text, "at": Time.get_datetime_string_from_system(true)}
	_timeline.add_gap_mark(t.at)
	_timeline.add_turn(t, false)
	_timeline.show_typing()

func add_reply(turn: Dictionary) -> void:
	_timeline.hide_typing()
	_timeline.add_gap_mark(turn.at)
	_timeline.add_turn(turn, false, true)
	_timeline.scroll_to_end()

func add_note(text: String, action := Callable()) -> void:
	_timeline.hide_typing()
	if action.is_valid():
		_timeline.add_action(text, "去设置", action)
	else:
		_timeline.add_divider(text)
	_timeline.scroll_to_end()

## Put back a line that was not sent, to send again later.
func restore(text: String) -> void:
	if _box.text.strip_edges().is_empty():
		_box.text = text
		_box.set_caret_column(text.length())

func set_waiting(waiting: bool) -> void:
	_waiting = waiting
	if _state == null:
		return
	_state.text = "正在想…" if waiting else ""
	_send.disabled = waiting
