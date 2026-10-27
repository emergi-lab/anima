class_name ChatInput
extends Window
## The input bar beside the pet: type, Enter sends, Esc closes. Locked while an answer is on
## its way. Also opens the chat log.

signal sent(text: String)
signal log_requested
## ⤢: carry on in the chat window with what is typed so far.
signal expand_requested(text: String)
signal closed

const BASE_SIZE := Vector2i(470, 58)

## The App autoload, injected on creation (a global name would not resolve in test scripts).
var app: Node
var _line: LineEdit
var _send: Button
var _hint := ""

func _init() -> void:
	# On Windows, borderless native child windows can appear as empty gray blocks
	# even when their viewport texture is correct. Use the tested native frame.
	var native_frame := OS.get_name() == "Windows"
	borderless = not native_frame
	transparent = not native_frame
	transparent_bg = transparent
	minimize_disabled = true
	maximize_disabled = true
	close_requested.connect(close)
	unresizable = true
	visible = false
	title = "Anima · 对话"
	size = BASE_SIZE

func _ready() -> void:
	var panel := PanelContainer.new()
	panel.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var box := StyleBoxFlat.new()
	box.bg_color = UI.C_SURFACE
	box.border_color = UI.C_FRAME
	box.set_border_width_all(UI.FRAME_W)
	box.set_corner_radius_all(0)  # square: round corners would leave see-through pixels
	box.set_content_margin_all(8)
	panel.add_theme_stylebox_override("panel", box)
	add_child(panel)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 6)
	panel.add_child(row)
	_line = LineEdit.new()
	_line.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_line.max_length = 500
	_line.text_submitted.connect(func(_t: String) -> void: _submit())
	row.add_child(_line)
	_send = Button.new()
	_send.text = "说"
	_send.tooltip_text = "发送（回车）"
	_send.pressed.connect(_submit)
	row.add_child(_send)
	var expand := Button.new()
	expand.text = "⤢"
	expand.tooltip_text = "放大成聊天窗口"
	expand.add_theme_font_size_override("font_size", 22)  # the arrow glyph is small otherwise
	expand.pressed.connect(func() -> void:
		var text := _line.text
		_line.clear()
		expand_requested.emit(text))
	row.add_child(expand)
	var log := Button.new()
	log.text = "记录"
	log.tooltip_text = "聊天记录"
	log.pressed.connect(func() -> void: log_requested.emit())
	row.add_child(log)
	var x := Button.new()
	x.text = "✕"
	x.tooltip_text = "收起（Esc）"
	x.pressed.connect(close)
	row.add_child(x)

func open_for(persona_name: String) -> void:
	_hint = "跟%s说点什么…" % persona_name
	_line.placeholder_text = _hint
	if not visible:
		show()
		always_on_top = true  # set before show() it comes back false on macOS: under other apps
	grab_focus()
	_line.grab_focus()

func close() -> void:
	if visible:
		hide()
		closed.emit()

## Locked while the persona is answering.
func set_waiting(waiting: bool, persona_name := "") -> void:
	_line.editable = not waiting
	_send.disabled = waiting
	_line.placeholder_text = "%s在想…" % persona_name if waiting else _hint
	if not waiting and visible:
		_line.grab_focus()

## Put back a line that was not sent, to send again later.
func restore(text: String) -> void:
	if _line.text.strip_edges().is_empty():
		_line.text = text
		_line.caret_column = text.length()

func _submit() -> void:
	var text := _line.text.strip_edges()
	if text.is_empty() or not _line.editable:
		return
	_line.clear()
	sent.emit(text)

func _input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and event.keycode == KEY_ESCAPE:
		close()
