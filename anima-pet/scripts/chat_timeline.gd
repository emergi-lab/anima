class_name ChatTimeline
extends ScrollContainer
## Chat lines in order: the owner's on the right; the persona's on the left with its icon, mood
## and a "内心" button that opens what was going on inside it then (the state it answered from).
## Shared by the chat log (date lines, each chat's start time) and the chat window (a time
## only after a 5-minute gap, a typing bubble, replies typed out).

const BUBBLE_W := 520.0
const GAP_SECONDS := 300
const CHAR_SECONDS := 0.045
const NEED_NOW := {"satisfied": "满足", "frustrated": "受挫", "steady": "平稳"}
const DIRECTION := {"higher": "比平时更强", "lower": "比平时更弱", "typical": "和平时一样"}
const CHANNEL := {"feeling": "情绪", "attention": "注意力", "language": "说话", "action": "行动"}

signal more_requested

## The App autoload, injected on creation (a global name would not resolve in test scripts).
var app: Node
var bubble_width := BUBBLE_W
## Narrow layouts (the chat window): the inner-state button is its icon only.
var icon_only := false
var _box: VBoxContainer
var _open: Control  # the inner-state card that is showing
var _typing: Control
var _typing_tween: Tween
var _last_at := -1  # unix time of the last line, for the 5-minute time marks
var _stick := true  # at the bottom: stay there as lines grow (a reply typing out, a card)

func _init() -> void:
	horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	clip_contents = true
	_box = VBoxContainer.new()
	_box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_box.add_theme_constant_override("separation", 10)
	add_child(_box)
	get_v_scroll_bar().value_changed.connect(_on_scrolled)
	get_v_scroll_bar().changed.connect(_on_content_resized)

func _on_scrolled(value: float) -> void:
	var bar := get_v_scroll_bar()
	_stick = value >= bar.max_value - bar.page - 4.0

func _on_content_resized() -> void:
	if _stick:
		scroll_vertical = int(get_v_scroll_bar().max_value)

func clear(more := false) -> void:
	for c in _box.get_children():
		_box.remove_child(c)
		c.queue_free()
	_open = null
	hide_typing()
	_last_at = -1
	if more:
		var b := Button.new()
		b.text = "加载更早的消息"
		b.flat = true
		b.add_theme_color_override("font_color", UI.C_PRIMARY)
		b.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
		b.pressed.connect(func() -> void: more_requested.emit())
		_box.add_child(b)

func add_divider(text: String, font_size := 13) -> void:
	var l := UI.label(text, font_size, UI.C_MUTED)
	l.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_box.add_child(l)

## A muted line with a button under it (go and fix something).
func add_action(text: String, label: String, action: Callable) -> void:
	add_divider(text)
	var b := Button.new()
	b.text = label
	b.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	b.pressed.connect(action)
	_box.add_child(b)

## A time mark when this line comes more than 5 minutes after the last one (chat window).
func add_gap_mark(iso: Variant) -> void:
	var t := int(Time.get_unix_time_from_datetime_string(str(iso).left(19)))
	if _last_at < 0 or t - _last_at >= GAP_SECONDS:
		var local := UI.local_time(iso)
		var today := Time.get_date_string_from_system()
		add_divider(local.substr(11, 5) if local.left(10) == today else
			"%d月%d日 %s" % [int(local.substr(5, 2)), int(local.substr(8, 2)), local.substr(11, 5)])
	_last_at = t

func lines() -> Array:
	return _box.get_children().filter(func(c: Node) -> bool: return c.has_meta("turn"))

func last_line() -> Control:
	var all := lines()
	return all[-1] if not all.is_empty() else null

## One line; `type_out` shows the text a character at a time (a reply that just arrived).
func add_turn(t: Dictionary, show_time := true, type_out := false) -> Control:
	var mine: bool = t.speaker == "owner"
	var wrap := VBoxContainer.new()
	wrap.set_meta("turn", t)
	wrap.add_theme_constant_override("separation", 6)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	wrap.add_child(row)
	var spacer := Control.new()
	spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var bubble := _bubble(str(t.text), mine)
	var col := VBoxContainer.new()
	col.add_theme_constant_override("separation", 2)
	if mine:
		row.add_child(spacer)
		row.add_child(col)
		if show_time:
			var meta := UI.label(_clock(t.get("at")), 12, UI.C_MUTED)
			meta.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
			col.add_child(meta)
		col.add_child(bubble)
	else:
		row.add_child(_icon())
		row.add_child(col)
		var parts := PackedStringArray()
		for part in [feel_of(t), _clock(t.get("at")) if show_time else ""]:
			if not part.is_empty():
				parts.append(part)
		var meta_text := " · ".join(parts)
		if not meta_text.is_empty():
			col.add_child(UI.label(meta_text, 12, UI.C_MUTED))
		col.add_child(bubble)
		if t.get("action_id") != null:
			bubble.mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
			bubble.tooltip_text = "看看它当时心里在想什么"
			bubble.gui_input.connect(func(e: InputEvent) -> void:
				if e is InputEventMouseButton and e.pressed and e.button_index == MOUSE_BUTTON_LEFT:
					toggle_inner(wrap, t))
			row.add_child(_inner_button(wrap, t))
		row.add_child(spacer)
	_box.add_child(wrap)
	if type_out:
		var label: Label = bubble.get_child(0)
		# lay out the whole text first, so the bubble has its full height from the start
		label.visible_characters_behavior = TextServer.VC_CHARS_AFTER_SHAPING
		label.visible_characters = 0
		var tween := create_tween()
		tween.tween_property(label, "visible_characters", label.text.length(),
			label.text.length() * CHAR_SECONDS)
		tween.tween_callback(func() -> void: label.visible_characters = -1)
	return wrap

## "· · ·" on the persona's side while an answer is on its way.
func show_typing() -> void:
	hide_typing()
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	row.add_child(_icon())
	var bubble := _bubble("·  ·  ·", false)
	row.add_child(bubble)
	_typing = row
	_box.add_child(row)
	var label: Label = bubble.get_child(0)
	_typing_tween = create_tween().set_loops()
	for n in [1, 4, 7]:
		_typing_tween.tween_property(label, "visible_characters", n, 0.0)
		_typing_tween.tween_interval(0.4)
	scroll_to_end()

func hide_typing() -> void:
	if _typing_tween != null:
		_typing_tween.kill()
		_typing_tween = null
	if _typing != null and is_instance_valid(_typing):
		_box.remove_child(_typing)
		_typing.queue_free()
	_typing = null

func scroll_to_end() -> void:
	_stick = true
	await get_tree().process_frame
	await get_tree().process_frame
	scroll_vertical = int(get_v_scroll_bar().max_value)

func _bubble(text: String, mine: bool) -> PanelContainer:
	var bubble := PanelContainer.new()
	bubble.add_theme_stylebox_override("panel", bubble_box(mine))
	var label := UI.label(text, 15)
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	var w := UI.body_font().get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, 15).x
	label.custom_minimum_size.x = minf(bubble_width, ceilf(w) + 2)
	bubble.add_child(label)
	return bubble

func _icon() -> TextureRect:
	var icon := TextureRect.new()
	icon.texture = app.persona_icon(app.persona_id, 2)
	icon.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
	icon.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	icon.custom_minimum_size = Vector2(64, 44)
	icon.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	return icon

func _inner_button(wrap: Control, t: Dictionary) -> Button:
	var b := Button.new()
	b.name = "InnerButton"
	b.flat = true
	b.text = "" if icon_only else "内心"
	b.icon = _thought_icon()
	b.tooltip_text = "看看它当时心里在想什么"
	b.mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	b.add_theme_color_override("font_color", UI.C_PRIMARY)
	b.add_theme_color_override("font_hover_color", UI.C_PRIMARY)
	b.add_theme_font_size_override("font_size", 13)
	b.size_flags_vertical = Control.SIZE_SHRINK_END
	b.pressed.connect(func() -> void: toggle_inner(wrap, t))
	return b

## The first frame of the "thinking" emote: the hint that a line can be opened.
static func _thought_icon() -> Texture2D:
	var sheet := Avatar.emote_texture("think")
	if sheet == null:
		return null
	var frame := AtlasTexture.new()
	frame.atlas = sheet
	frame.region = Rect2(0, 0, Avatar.FRAME, Avatar.FRAME)
	return frame

static func bubble_box(mine: bool) -> StyleBoxFlat:
	var b := StyleBoxFlat.new()
	b.bg_color = UI.C_MINE_BG if mine else UI.C_INPUT
	b.border_color = Color(UI.C_FRAME, 0.6)
	b.set_border_width_all(2)
	b.set_corner_radius_all(6)
	b.content_margin_left = 12
	b.content_margin_right = 12
	b.content_margin_top = 8
	b.content_margin_bottom = 8
	return b

static func feel_of(t: Dictionary) -> String:
	return str(PetChat.EMOTIONS.get(t.get("emotion"), [""])[0]) if t.get("emotion") != null else ""

static func _clock(iso: Variant) -> String:
	return UI.local_time(iso).substr(11, 5)

# ---- inner state ----------------------------------------------------------------------------

func toggle_inner(wrap: VBoxContainer, t: Dictionary) -> void:
	var card: Control = wrap.get_node_or_null("Inner")
	if card != null:  # this one is open: close it
		wrap.remove_child(card)
		card.queue_free()
		_open = null
		_mark(wrap, false)
		return
	if _open != null and is_instance_valid(_open):
		_mark(_open.get_parent(), false)
		_open.get_parent().remove_child(_open)
		_open.queue_free()
	_mark(wrap, true)
	var panel := PanelContainer.new()
	var box := StyleBoxFlat.new()
	box.bg_color = Color("#fff3d6")
	box.border_width_left = 4
	box.border_color = UI.C_PRIMARY
	box.set_content_margin_all(10)
	box.content_margin_left = 14
	panel.add_theme_stylebox_override("panel", box)
	var margin := MarginContainer.new()  # under the bubble, past the icon
	margin.name = "Inner"
	margin.add_theme_constant_override("margin_left", 72)
	margin.add_theme_constant_override("margin_right", 40)
	var col := VBoxContainer.new()
	col.add_theme_constant_override("separation", 4)
	panel.add_child(col)
	margin.add_child(panel)
	wrap.add_child(margin)
	_open = margin
	col.add_child(UI.heading("它当时的内心", 15))
	var body := UI.label("读取中…", 14, UI.C_MUTED)
	body.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	col.add_child(body)
	_reveal(margin)
	for attempt in 6:  # the newest line's state is stored a few seconds after it is shown
		var r: Dictionary = await app.api.request_json("/v1/actions/%s/state" % str(t.action_id).uri_encode())
		if not is_instance_valid(body):
			return
		if r.ok:
			_show_inner(col, body, r.data, t)
			_reveal(margin)
			return
		if int(r.get("status", 0)) != 404:
			body.text = str(r.error)
			return
		body.text = "还在整理…"
		await get_tree().create_timer(2.0).timeout
	if is_instance_valid(body):
		body.text = "这句话当时没留下内心记录。"

## The state in plain words: how it saw the moment, what it cared about, what it meant to do,
## which traits showed, and its mood.
func _show_inner(col: VBoxContainer, body: Label, data: Dictionary, t: Dictionary) -> void:
	var st: Dictionary = data.state
	var l3: Dictionary = st.get("L3", {})
	var l2: Dictionary = st.get("L2", {})
	var l1: Dictionary = st.get("L1", {})
	var out := PackedStringArray()
	var seen := str(l3.get("event_meaning", "")) if l3.get("event_meaning") else str(l3.get("identity_position", ""))
	if not seen.is_empty():
		out.append("怎么看这件事：" + seen)
	for n in l2.get("needs", []):
		out.append("在意的：%s（%s）%s" % [PersonaWindow.name_of(str(n.get("construct_id", ""))),
			NEED_NOW.get(n.get("now"), str(n.get("now", ""))),
			" — " + str(n.cause) if n.get("cause") else ""])
	for v in l2.get("values_activated", []):
		out.append("看重的：%s%s" % [PersonaWindow.name_of(str(v.get("construct_id", ""))),
			" — " + str(v.why) if v.get("why") else ""])
	var plan: Dictionary = l2.get("strategy", {}) if l2.get("strategy") is Dictionary else {}
	if plan.get("chosen"):
		out.append("打算：%s%s" % [plan.chosen, " — " + str(plan.why) if plan.get("why") else ""])
	for x in l1.get("expressions", []):
		out.append("表现：%s%s，体现在%s上" % [PersonaWindow.name_of(str(x.get("construct_id", ""))),
			DIRECTION.get(x.get("direction"), ""), CHANNEL.get(x.get("channel"), "言行")])
	if out.is_empty():
		body.text = "这句话当时没留下内心记录。"
		return
	var feel := feel_of(t)
	if not feel.is_empty():
		out.append("心情：" + feel)
	col.remove_child(body)
	body.queue_free()
	for line in out:
		var l := UI.label(line, 14)
		l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		col.add_child(l)

func _mark(wrap: Node, open: bool) -> void:
	var button: Button = wrap.find_child("InnerButton", true, false)
	if button != null and not icon_only:
		button.text = "收起" if open else "内心"

## Scroll so an opened card is in view once it has its size.
func _reveal(card: Control) -> void:
	await get_tree().process_frame
	await get_tree().process_frame
	if is_instance_valid(card):
		ensure_control_visible(card)
