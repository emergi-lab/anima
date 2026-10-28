class_name SettingsWindow
extends Window
## 设置: the pet (interface size, name tag, wandering) and the model service the persona thinks
## with — the owner fills in an OpenAI-compatible endpoint, model name and their own key.
## The thinking switch keeps its server setting. Pet options apply at
## once; the model service is tried with "测试连接" and takes effect on "保存", no restart.
## The key stays in the backend's local data/model.json and is never shown again.

const BASE_SIZE := Vector2i(720, 780)

## The App autoload, injected on creation (a global name would not resolve in test scripts).
var app: Node
var _zoom: OptionButton
var _name_tag: CheckBox
var _wander: CheckBox
var _hint: CheckBox
var _current: Label
var _url: LineEdit
var _key: LineEdit
var _model: LineEdit
var _result: Label
var _test: Button
var _save: Button
var _view: Dictionary = {}
var _focus_model := false  # point at the key once the settings have loaded

func _init() -> void:
	title = "Anima · 设置"
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
	root.add_theme_constant_override("separation", 14)
	margin.add_child(root)
	root.add_child(UI.pixel_label("设置", 26))
	root.add_child(_build_pet())
	root.add_child(_build_model())
	var backend := UI.label("桌宠连接的后端：%s（启动桌宠前可用环境变量 ANIMA_API 修改）" % app.api.base_url,
		13, UI.C_MUTED)
	backend.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	root.add_child(backend)
	app.settings_changed.connect(_show_pet)

func _card(heading: String) -> VBoxContainer:
	var panel := PanelContainer.new()
	var box := StyleBoxFlat.new()
	box.bg_color = UI.C_SURFACE
	box.set_border_width_all(2)
	box.border_color = Color(UI.C_FRAME, 0.6)
	box.set_corner_radius_all(4)
	box.set_content_margin_all(14)
	panel.add_theme_stylebox_override("panel", box)
	var col := VBoxContainer.new()
	col.add_theme_constant_override("separation", 10)
	panel.add_child(col)
	col.add_child(UI.heading(heading, 18))
	col.set_meta("panel", panel)
	return col

func _row(grid: GridContainer, label: String, control: Control) -> void:
	grid.add_child(UI.label(label, 15, UI.C_MUTED))
	control.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	grid.add_child(control)

# ---- the pet --------------------------------------------------------------------------------

func _build_pet() -> Control:
	var col := _card("桌宠")
	var grid := GridContainer.new()
	grid.columns = 2
	grid.add_theme_constant_override("h_separation", 14)
	grid.add_theme_constant_override("v_separation", 10)
	col.add_child(grid)
	_zoom = OptionButton.new()
	for z in app.ZOOMS:
		_zoom.add_item(str(z[1]))
	_zoom.item_selected.connect(func(i: int) -> void: app.set_option("zoom", app.ZOOMS[i][0]))
	_row(grid, "界面大小", _zoom)
	_name_tag = CheckBox.new()
	_name_tag.text = "在小人脚下显示名字"
	_name_tag.toggled.connect(func(on: bool) -> void: app.set_option("show_name", on))
	_row(grid, "名牌", _name_tag)
	_wander = CheckBox.new()
	_wander.text = "沿屏幕底边走来走去"
	_wander.toggled.connect(func(on: bool) -> void: app.set_option("wander", on))
	_row(grid, "闲逛", _wander)
	_hint = CheckBox.new()
	_hint.text = "聊过 5 段以后，提醒我让它反思一下"
	_hint.toggled.connect(func(on: bool) -> void: app.set_option("reflect_hint", on))
	_row(grid, "反思提醒", _hint)
	return col.get_meta("panel")

func _show_pet() -> void:
	for i in app.ZOOMS.size():
		if is_equal_approx(app.ZOOMS[i][0], app.zoom):
			_zoom.select(i)
	_name_tag.set_pressed_no_signal(app.show_name)
	_wander.set_pressed_no_signal(app.wander)
	_hint.set_pressed_no_signal(app.reflect_hint)

# ---- the model service ----------------------------------------------------------------------

func _build_model() -> Control:
	var col := _card("模型服务（你自己的 API）")
	var hint := UI.label("人格用它来说话、蒸馏和演化。支持任何 OpenAI 兼容接口：填接口地址、模型名和 API Key，先测试连接再保存，保存后立即生效。Key 只保存在本机后端数据目录的 data/model.json（仅你的账户可读），不会再显示出来。",
		13, UI.C_MUTED)
	hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	col.add_child(hint)
	_current = UI.label("读取中…", 14)
	_current.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	col.add_child(_current)
	var grid := GridContainer.new()
	grid.columns = 2
	grid.add_theme_constant_override("h_separation", 14)
	grid.add_theme_constant_override("v_separation", 10)
	col.add_child(grid)
	_url = LineEdit.new()
	_url.placeholder_text = "https://…/v1"
	_row(grid, "接口地址", _url)
	_key = LineEdit.new()
	_key.secret = true
	_row(grid, "API Key", _key)
	_model = LineEdit.new()
	_model.name = "ModelName"
	_model.placeholder_text = "请输入服务商提供的模型 ID"
	_model.max_length = 120
	_row(grid, "模型名", _model)
	var buttons := HBoxContainer.new()
	buttons.add_theme_constant_override("separation", 10)
	col.add_child(buttons)
	_test = Button.new()
	_test.text = "测试连接"
	_test.pressed.connect(_try)
	buttons.add_child(_test)
	_save = Button.new()
	_save.text = "保存"
	_save.pressed.connect(_store)
	buttons.add_child(_save)
	_result = UI.label("", 14, UI.C_MUTED)
	_result.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_result.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	buttons.add_child(_result)
	return col.get_meta("panel")

func reload() -> void:
	_show_pet()
	_result.text = ""
	var r: Dictionary = await app.api.request_json("/v1/settings/model")
	if not r.ok:
		_current.text = "读不到后端的设置：%s" % r.error
		return
	_show_model(r.data)
	app.set_model_ready(bool(r.data.get("ready", true)))
	if _focus_model:
		_focus_model = false
		_point_at_key()

## Opened from a reminder: put the cursor in the key and say what to do.
func focus_model() -> void:
	_focus_model = true
	if not _view.is_empty():
		_focus_model = false
		_point_at_key()

func _point_at_key() -> void:
	if bool(_view.get("key_set", false)):
		_say("Key 已经填过了，但服务不认：检查 Key、接口地址和模型名，再「测试连接」。", UI.C_ERROR)
		_key.grab_focus()
		return
	_say("填好接口地址、模型名和 API Key，先「测试连接」，成功后「保存」。", UI.C_ERROR)
	_key.grab_focus()

func _show_model(v: Dictionary) -> void:
	_view = v
	var where := "设置页" if v.source == "saved" else "anima-server/.env"
	_current.text = ("当前：%s\n模型：%s · Key %s（来自%s）" % [v.base_url, v.model, v.key_hint, where]) if v.key_set \
		else ("当前：还没有 API Key——聊天、蒸馏、演化和反思都要用它。填好接口地址、模型名和 API Key，测试连接后保存。"
			if v.backend == "openai" else "当前：开发用的离线测试模型（PERSONA_MODEL_BACKEND=fake，只会说固定的话）。")
	_url.text = str(v.base_url)
	_model.text = str(v.model)
	_key.text = ""
	_key.placeholder_text = "已设置 %s，留空表示不修改" % v.key_hint if v.key_set else "sk-…"

## No key means "keep the saved one"; preserve the server's thinking switch.
func _body() -> Dictionary:
	var body := {"base_url": _url.text.strip_edges(), "model": _model.text.strip_edges(),
		"disable_thinking": bool(_view.get("disable_thinking", true))}
	if not _key.text.strip_edges().is_empty():
		body["api_key"] = _key.text.strip_edges()
	return body

func _valid_model() -> bool:
	var url := _url.text.strip_edges()
	if not (url.begins_with("https://") or url.begins_with("http://")):
		_say("请填写接口地址（以 http:// 或 https:// 开头）。", UI.C_ERROR)
		_url.grab_focus()
		return false
	if _model.text.strip_edges().is_empty():
		_say("请填写模型名（服务商提供的模型 ID）。", UI.C_ERROR)
		_model.grab_focus()
		return false
	return true

func _busy(on: bool, text := "") -> void:
	_test.disabled = on
	_save.disabled = on
	_model.editable = not on
	if not text.is_empty():
		_say(text)

func _say(text: String, color := UI.C_MUTED) -> void:
	_result.text = text
	_result.add_theme_color_override("font_color", color)

func _try() -> void:
	if not _valid_model():
		return
	_busy(true, "测试中…")
	var r: Dictionary = await app.api.request_json("/v1/settings/model:test",
		HTTPClient.METHOD_POST, _body(), 40.0)
	_busy(false)
	if not r.ok:
		_say(str(r.error), UI.C_ERROR)
	elif r.data.ok:
		_say("连接成功（%.1f 秒）" % (float(r.data.latency_ms) / 1000.0), Color("#2f6b2a"))
	else:
		_say(str(r.data.message), UI.C_ERROR)

func _store() -> void:
	if not _valid_model():
		return
	_busy(true, "保存中…")
	var r: Dictionary = await app.api.request_json("/v1/settings/model", HTTPClient.METHOD_PUT, _body())
	_busy(false)
	if not r.ok:
		_say("没有保存：%s" % r.error, UI.C_ERROR)
		return
	_show_model(r.data)
	app.set_model_ready(bool(r.data.get("ready", true)))
	_say("已保存，接下来的聊天、蒸馏和演化都用它。", Color("#2f6b2a"))
