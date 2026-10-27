class_name PersonaManager
extends Window
## 管理人格: every persona the owner has (at most App.max_personas), to switch to or delete.
## Deleting is for good — its versions, chats and memories — but its distillation stays in the
## workbench and can be imported again.

const BASE_SIZE := Vector2i(760, 620)

## The App autoload, injected on creation (a global name would not resolve in test scripts).
var app: Node
var _count: Label
var _rows: VBoxContainer
var _status: Label
var _new: Button
var _confirm: ConfirmationDialog
var _doomed := {}  # the persona the confirmation is about

func _init() -> void:
	title = "Anima · 管理人格"
	size = BASE_SIZE
	min_size = BASE_SIZE
	wrap_controls = false
	close_requested.connect(hide)

func _ready() -> void:
	var bg := Panel.new()
	bg.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var sb := StyleBoxFlat.new()
	sb.bg_color = UI.C_BG
	bg.add_theme_stylebox_override("panel", sb)
	add_child(bg)
	var margin := MarginContainer.new()
	margin.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	for side in ["left", "right", "top", "bottom"]:
		margin.add_theme_constant_override("margin_" + side, 18)
	add_child(margin)
	var root := VBoxContainer.new()
	root.add_theme_constant_override("separation", 12)
	margin.add_child(root)
	var head := HBoxContainer.new()
	root.add_child(head)
	var title_label := UI.pixel_label("管理人格", 24)
	title_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	head.add_child(title_label)
	_count = UI.label("", 15, UI.C_MUTED)
	_count.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	head.add_child(_count)
	var scroll := ScrollContainer.new()
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	root.add_child(scroll)
	_rows = VBoxContainer.new()
	_rows.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_rows.add_theme_constant_override("separation", 10)
	scroll.add_child(_rows)
	_status = UI.label("", 14, UI.C_MUTED)
	_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	root.add_child(_status)
	var foot := HBoxContainer.new()
	root.add_child(foot)
	_new = Button.new()
	_new.text = "蒸馏一个新的…"
	_new.pressed.connect(func() -> void:
		hide()
		app.open_workbench())
	foot.add_child(_new)
	_confirm = ConfirmationDialog.new()
	_confirm.title = "永久删除人格"
	_confirm.ok_button_text = "永久删除"
	_confirm.cancel_button_text = "取消"
	_confirm.confirmed.connect(_delete)
	add_child(_confirm)
	app.personas_changed.connect(_fill)
	app.look_changed.connect(_fill)
	_fill()

func _fill() -> void:
	for c in _rows.get_children():
		_rows.remove_child(c)
		c.queue_free()
	var n: int = app.personas.size()
	var full: bool = app.max_personas > 0 and n >= app.max_personas
	_count.text = "已用 %d / %d 个" % [n, app.max_personas] if app.max_personas > 0 else "%d 个" % n
	_new.disabled = full
	_new.tooltip_text = "最多 %d 个人格，先删除一个" % app.max_personas if full else ""
	if n == 0:
		_rows.add_child(UI.label("还没有人格。在蒸馏工作台蒸馏并导入一个。", 15, UI.C_MUTED))
	for p in app.personas:
		_rows.add_child(_row(p))

func _row(p: Dictionary) -> Control:
	var current: bool = str(p.persona_id) == app.persona_id
	var panel := PanelContainer.new()
	var box := StyleBoxFlat.new()
	box.bg_color = UI.C_SURFACE
	box.set_border_width_all(3 if current else 2)
	box.border_color = UI.C_PRIMARY if current else Color(UI.C_FRAME, 0.6)
	box.set_corner_radius_all(4)
	box.set_content_margin_all(10)
	panel.add_theme_stylebox_override("panel", box)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 14)
	panel.add_child(row)
	var icon := TextureRect.new()
	icon.texture = app.persona_icon(str(p.persona_id), 3)
	icon.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	icon.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
	icon.custom_minimum_size = Vector2(96, 66)
	row.add_child(icon)
	var col := VBoxContainer.new()
	col.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	col.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(col)
	col.add_child(UI.heading(str(p.name) + ("（当前）" if current else ""), 18))
	col.add_child(UI.label("B%d · %d 条信念 · %d 段聊天 · %s" % [int(p.k), int(p.entries),
		int(p.chats), app.ago(p.last_chat_at)], 14, UI.C_MUTED))
	var switch := Button.new()
	switch.text = "当前" if current else "切换"
	switch.disabled = current
	switch.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	switch.pressed.connect(func() -> void: app.switch_persona(str(p.persona_id), str(p.name)))
	row.add_child(switch)
	var remove := Button.new()
	remove.text = "删除…"
	remove.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	remove.add_theme_color_override("font_color", UI.C_ERROR)
	remove.pressed.connect(func() -> void: _ask(p))
	row.add_child(remove)
	return panel

func _ask(p: Dictionary) -> void:
	_doomed = p
	_confirm.dialog_text = ("永久删除「%s」？\n\n它的人格版本、聊天记录和记忆都会删除，无法恢复。\n"
		+ "蒸馏用的素材和审阅结果保留在工作台，可以重新导入成新的「%s」，\n但之前的聊天和记忆不会回来。") % [p.name, p.name]
	app.popup_dialog(_confirm, Vector2i(560, 260), self)

func _delete() -> void:
	var p := _doomed
	_status.text = "正在删除「%s」…" % p.name
	var r: Dictionary = await app.delete_persona(str(p.persona_id))
	if not r.ok:
		_status.text = "没有删掉：%s" % ("它正在蒸馏或演化，等结束后再删。" if r.get("code") == "persona_busy" else str(r.error))
		return
	_status.text = "已删除「%s」。" % p.name
