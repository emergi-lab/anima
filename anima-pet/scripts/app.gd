extends Node
## Autoload "App": backend client, local settings (active persona, its look, pet behaviour),
## the shared parchment theme and the windows the pet opens.

signal look_changed
signal settings_changed
## A watched evolution was polled (its Distillation); `evolution_finished` once it stops running.
signal evolution_updated(d: Dictionary)
signal evolution_finished(d: Dictionary)
## The persona's beliefs changed (evolution committed or an earlier version restored).
signal persona_changed(line: String)
## Something asked the pet to open its chat (the workbench's "和它说话").
signal talk_requested
## The roster (`personas`) was reloaded.
signal personas_changed
## A switch to another persona is starting (close the chat windows) / has happened.
signal persona_switching
signal persona_switched
## A watched reflection was polled / stopped running (its Reflection).
signal reflection_updated(d: Dictionary)
signal reflection_finished(d: Dictionary)
## The model service became usable or unusable (`model_ready`).
signal model_ready_changed

## Said whenever the persona would need the model and no API key is set yet (short: it has to
## fit one page of the head bubble).
const NO_MODEL := "还没有填模型 API，我没法思考。"

const SETTINGS := "user://anima.cfg"
const DEFAULT_PERSONA_NAME := "anima"
## Interface sizes offered in the pet menu, on top of the screen's own HiDPI scale.
const ZOOMS := [[1.0, "小"], [1.25, "标准"], [1.5, "大"], [1.75, "特大"]]

var api: Backend
var theme: Theme
## Persona the pet speaks as; set when a distillation is committed.
var persona_id := ""
var persona_name := DEFAULT_PERSONA_NAME
var wander := true
var show_name := false
var zoom := 1.25
## Suggest a reflection once enough chats have piled up since the current version (off by default).
var reflect_hint := false
var _cfg := ConfigFile.new()
var _settings_path := SETTINGS
var _workbench: Workbench
var _picker: AvatarPicker
var _chat_log: ChatLog
var _persona: PersonaWindow
var _manager: PersonaManager
var _settings: SettingsWindow
## The owner's personas from the server, most recently chatted first, and the limit.
var personas: Array = []
var max_personas := 5
var _roster_request := 0  # ignore older responses and selection changes during a refresh
## The server has a key for the model (or runs the offline fake); unknown until `check_model`.
var model_ready := true
var _icons := {}  # persona id + look → menu icon
## Hitches: a frame over HITCH_MS is written to user://hitches.log with what was open and the
## last things done, so a freeze the owner saw can be traced afterwards.
const HITCH_MS := 1000
const HITCH_LOG := "user://hitches.log"
var _frame_at := 0
var _recent: PackedStringArray = []
var _review_fast_input := false
var _resting_max_fps := 0
var _resting_accumulated_input := true
var _watching := ""  # the evolution being polled
var _watch := Timer.new()
var _watching_reflection := ""
var _reflect_watch := Timer.new()

## Remember what the owner just did (menu items, buttons), for the hitch log.
func note(action: String) -> void:
	_recent.append("%s %s" % [Time.get_time_string_from_system(), action])
	if _recent.size() > 8:
		_recent.remove_at(0)

func _process(_delta: float) -> void:
	_update_review_pacing()
	var now := Time.get_ticks_msec()
	if _frame_at > 0 and now - _frame_at > HITCH_MS:
		var open := PackedStringArray()
		for w in [_workbench, _picker, _chat_log, _persona, _manager, _settings]:
			if w != null and w.visible:
				open.append(w.title)
		var f := FileAccess.open(HITCH_LOG, FileAccess.READ_WRITE if FileAccess.file_exists(HITCH_LOG)
			else FileAccess.WRITE)
		if f != null:
			f.seek_end()
			f.store_line("%s 卡了 %d 毫秒 · 屏幕 %d 块 · 打开的窗口：%s · 最近操作：%s" % [
				Time.get_datetime_string_from_system(false, true), now - _frame_at,
				DisplayServer.get_screen_count(), "、".join(open), " / ".join(_recent)])
	_frame_at = now

## Windows' separate OpenGL windows otherwise wait for V-Sync before presenting
## selection feedback. The project disables that wait on Windows and caps idle FPS;
## only the focused workbench uses the faster input/render cadence.
func _update_review_pacing() -> void:
	if OS.get_name() != "Windows":
		return
	var active := is_instance_valid(_workbench) and _workbench.visible and _workbench.has_focus()
	if active == _review_fast_input:
		return
	_review_fast_input = active
	Engine.max_fps = 120 if active else _resting_max_fps
	Input.use_accumulated_input = false if active else _resting_accumulated_input

func _ready() -> void:
	_resting_max_fps = Engine.max_fps
	_resting_accumulated_input = Input.use_accumulated_input
	api = Backend.new()
	api.name = "Api"
	add_child(api)
	theme = _build_theme()
	get_tree().root.theme = theme
	_load_settings()
	_watch.wait_time = 3.0
	_watch.timeout.connect(_poll_evolution)
	add_child(_watch)
	_reflect_watch.wait_time = 3.0
	_reflect_watch.timeout.connect(_poll_reflection)
	add_child(_reflect_watch)

## Tests switch to a throwaway file so they never touch the user's pet.
func use_settings(path: String) -> void:
	DirAccess.remove_absolute(ProjectSettings.globalize_path(path))
	_settings_path = path
	_cfg = ConfigFile.new()
	_load_settings()

func _load_settings() -> void:
	_cfg.load(_settings_path)
	persona_id = str(_cfg.get_value("pet", "persona_id", ""))
	persona_name = str(_cfg.get_value("pet", "persona_name", DEFAULT_PERSONA_NAME))
	if persona_id.is_empty() or persona_name.strip_edges().is_empty():
		persona_name = DEFAULT_PERSONA_NAME
	wander = bool(_cfg.get_value("pet", "wander", true))
	show_name = bool(_cfg.get_value("pet", "show_name", false))
	zoom = float(_cfg.get_value("pet", "zoom", 1.25))
	reflect_hint = bool(_cfg.get_value("pet", "reflect_hint", false))

func _save() -> void:
	_cfg.save(_settings_path)

func set_persona(id: String, display_name: String) -> void:
	var label := display_name.strip_edges()
	if id.is_empty() or label.is_empty():
		label = DEFAULT_PERSONA_NAME
	var changed := id != persona_id or label != persona_name
	persona_id = id
	persona_name = label
	_cfg.set_value("pet", "persona_id", id)
	_cfg.set_value("pet", "persona_name", label)
	_save()
	look_changed.emit()
	if changed:  # open windows follow the persona the pet now is
		if _persona != null and _persona.visible:
			_persona.reload()
		if _chat_log != null and _chat_log.visible:
			_chat_log.reload()

## Become another persona: the current chat ends first (so it turns into memories), then the
## pet takes the new name and look. Used by the menu, the manager and the workbench's import.
func switch_persona(id: String, display_name: String) -> void:
	if id == persona_id:
		set_persona(id, display_name)
		return
	persona_switching.emit()
	if not persona_id.is_empty():
		await api.request_json("/v1/personas/%s/chat:end" % persona_id.uri_encode(),
			HTTPClient.METHOD_POST, {}, 3.0)
	set_persona(id, display_name)
	persona_switched.emit()
	refresh_personas()

func refresh_personas(reconcile_active := true) -> void:
	_roster_request += 1
	var request := _roster_request
	var selected := persona_id
	var r: Dictionary = await api.request_json("/v1/personas")
	if not r.ok or request != _roster_request:
		return  # A failed request is not evidence that a saved persona was deleted.
	var data: Variant = r.get("data")
	if not (data is Dictionary) or not (data.get("items") is Array):
		return
	for person in data.items:
		if not (person is Dictionary) or not (person.get("persona_id") is String):
			return
	personas = data.items
	max_personas = int(data.get("max")) if data.get("max") != null else 0
	if reconcile_active and persona_id == selected and not selected.is_empty():
		var found := false
		for person in personas:
			if str(person.persona_id) == selected:
				found = true
				var label := str(person.get("name", persona_name)).strip_edges()
				if not label.is_empty() and label != persona_name:
					set_persona(selected, label)
				break
		if not found:
			# Godot user:// may contain a selection from a different backend/database.
			# Only clear the selection; never delete real personas, looks or model settings.
			persona_switching.emit()
			set_persona("", DEFAULT_PERSONA_NAME)
			persona_switched.emit()
	personas_changed.emit()

## Delete a persona for good; if it is the current one, the pet becomes the next one (or none).
func delete_persona(id: String, all_data := false) -> Dictionary:
	var r: Dictionary = await api.request_json("/v1/personas/" + id.uri_encode() + ("/all-data" if all_data else ""),
		HTTPClient.METHOD_DELETE)
	if not r.ok:
		return r
	# An older backend must never silently treat full deletion as persona-only deletion.
	if all_data and r.get("data", {}).get("status", "") != "deleted_all":
		return {"ok": false, "error": "后端未确认删除全部信息，请重启后端后重试。"}
	var key := _look_key(id)
	if _cfg.has_section_key("looks", key):
		_cfg.erase_section_key("looks", key)
		_save()
	for icon_key in _icons.keys():
		if str(icon_key).begins_with(id + "|"):
			_icons.erase(icon_key)
	# Preserve explicit delete's existing choice of the next persona.
	personas = personas.filter(func(p): return str(p.persona_id) != id)
	await refresh_personas(false)
	if id == persona_id:
		persona_switching.emit()
		var next: Dictionary = personas[0] if not personas.is_empty() else {}
		set_persona(str(next.get("persona_id", "")), str(next.get("name", "")))
		persona_switched.emit()
	return r

## Head-and-shoulders icon of a persona's look, cached per look.
func persona_icon(id: String, scale := 2) -> Texture2D:
	var key := "%s|%s|%d" % [id, str(look(id)), scale]
	if not _icons.has(key):
		_icons[key] = Avatar.icon(look(id), id, scale, 22)
	return _icons[key]

## "刚刚" / "5 分钟前" / "3 天前" for a server timestamp (UTC).
static func ago(iso: Variant) -> String:
	if iso == null:
		return "还没聊过"
	var t := Time.get_unix_time_from_datetime_string(str(iso).left(19))
	var s := int(Time.get_unix_time_from_system() - t)
	if s < 60:
		return "刚刚聊过"
	if s < 3600:
		return "%d 分钟前聊过" % (s / 60)
	if s < 86400:
		return "%d 小时前聊过" % (s / 3600)
	return "%d 天前聊过" % (s / 86400)

func set_option(key: String, value: Variant) -> void:
	set(key, value)
	_cfg.set_value("pet", key, value)
	_save()
	if key == "zoom":  # rescale whatever is open
		if _workbench != null and _workbench.visible:
			open_workbench()
		if _picker != null and _picker.visible:
			show_window(_picker, AvatarPicker.BASE_SIZE, AvatarPicker.BASE_SIZE)
		if _chat_log != null and _chat_log.visible:
			show_window(_chat_log, ChatLog.BASE_SIZE, ChatLog.MIN_SIZE)
		if _persona != null and _persona.visible:
			show_window(_persona, PersonaWindow.BASE_SIZE, PersonaWindow.MIN_SIZE)
		if _manager != null and _manager.visible:
			show_window(_manager, PersonaManager.BASE_SIZE, PersonaManager.BASE_SIZE)
		if _settings != null and _settings.visible:
			show_window(_settings, SettingsWindow.BASE_SIZE, SettingsWindow.BASE_SIZE)
	settings_changed.emit()

## Saved look of a persona, or null for the generated default (seeded by the persona id).
func look(id := persona_id) -> Variant:
	var key := _look_key(id)
	return _cfg.get_value("looks", key) if _cfg.has_section_key("looks", key) else null

func set_look(id: String, spec: Dictionary) -> void:
	_cfg.set_value("looks", _look_key(id), spec)
	_save()
	look_changed.emit()

## Where a window was left (screen pixels), or null the first time.
func window_rect(key: String) -> Variant:
	return _cfg.get_value("windows", key) if _cfg.has_section_key("windows", key) else null

## `rect` moved and shrunk to fit the work area of the screen it is mostly on (or the pet's), so a
## window saved with another display attached never opens partly off screen.
func fit_on_screen(rect: Rect2i) -> Rect2i:
	var on := screen()
	for i in DisplayServer.get_screen_count():
		if DisplayServer.screen_get_usable_rect(i).has_point(rect.get_center()):
			on = i
	var area := DisplayServer.screen_get_usable_rect(on)
	var title := roundi(32 * DisplayServer.screen_get_scale(on))  # the window's title bar
	area = Rect2i(area.position + Vector2i(0, title), area.size - Vector2i(0, title))
	var size := rect.size.min(area.size)
	var pos := rect.position.clamp(area.position, area.end - size)
	return Rect2i(pos, size)

func save_window_rect(key: String, rect: Rect2i) -> void:
	_cfg.set_value("windows", key, rect)
	_save()

## Remember that the reminder for this persona's version was given (once per version).
func hinted(key: String) -> bool:
	return _cfg.has_section_key("hints", key)

func mark_hinted(key: String) -> void:
	_cfg.set_value("hints", key, true)
	_save()

func _look_key(id: String) -> String:
	return id if not id.is_empty() else "_default"

func open_workbench() -> void:
	if _workbench == null:
		_workbench = Workbench.new()
		_workbench.app = self
		_workbench.theme = theme
		get_tree().root.add_child(_workbench)
	show_window(_workbench, Workbench.BASE_SIZE, Workbench.MIN_SIZE)

func open_picker(id := persona_id) -> void:
	if _picker == null:
		_picker = AvatarPicker.new()
		_picker.app = self
		_picker.theme = theme
		get_tree().root.add_child(_picker)
	_picker.edit(id)
	show_window(_picker, AvatarPicker.BASE_SIZE, AvatarPicker.BASE_SIZE)

## `beside`: the chat window it was opened from, which it must not cover.
func open_chat_log(beside: Window = null) -> void:
	if _chat_log == null:
		_chat_log = ChatLog.new()
		_chat_log.app = self
		_chat_log.theme = theme
		get_tree().root.add_child(_chat_log)
	show_window(_chat_log, ChatLog.BASE_SIZE, ChatLog.MIN_SIZE)
	if beside != null and beside.visible:
		_place_beside(_chat_log, beside)
		bring_forward(_chat_log)
	_chat_log.reload()

## Put `win` next to `other` (right, else left) so neither covers the other, shrinking `win` to
## the room there; when neither side has room, `other` moves to the right edge first. Only if
## the screen is too narrow for both at their smallest do they overlap.
func _place_beside(win: Window, other: Window) -> void:
	var area := DisplayServer.screen_get_usable_rect(other.current_screen)
	var gap := roundi(12 * other.content_scale_factor)
	var right := area.end.x - (other.position.x + other.size.x + gap)
	var left := other.position.x - gap - area.position.x
	if maxi(right, left) < win.min_size.x and other.size.x + gap + win.min_size.x <= area.size.x:
		other.position = Vector2i(area.end.x - other.size.x, other.position.y)
		right = -1
		left = other.position.x - gap - area.position.x
	var w := clampi(maxi(right, left), win.min_size.x, win.size.x)
	var x := other.position.x + other.size.x + gap if right >= left else other.position.x - gap - w
	win.size = Vector2i(w, mini(win.size.y, area.size.y))
	win.position = Vector2i(clampi(x, area.position.x, area.end.x - w),
		clampi(other.position.y, area.position.y, area.end.y - win.size.y))

func open_settings() -> void:
	if _settings == null:
		_settings = SettingsWindow.new()
		_settings.app = self
		_settings.theme = theme
		get_tree().root.add_child(_settings)
	show_window(_settings, SettingsWindow.BASE_SIZE, SettingsWindow.BASE_SIZE)
	_settings.reload()

## Open the settings at the model service, ready for the key.
func open_model_settings() -> void:
	open_settings()
	_settings.focus_model()

func check_model() -> void:
	var r: Dictionary = await api.request_json("/v1/settings/model", HTTPClient.METHOD_GET, null, 5.0)
	if r.ok:
		set_model_ready(bool(r.data.get("ready", true)))

func set_model_ready(ready: bool) -> void:
	if ready != model_ready:
		model_ready = ready
		model_ready_changed.emit()

## A failed request (or a job's error text) that only the model settings can fix: no key yet,
## or the service refused the key. The server words both the same way (docs/接口契约.md §10).
func model_trouble(r: Variant) -> bool:
	var code := str(r.get("code", "")) if r is Dictionary else ""
	var text := str(r.get("error", "")) if r is Dictionary else str(r)
	if code == "model_not_configured" or text.contains("还没有配置模型 API"):
		set_model_ready(false)
		return true
	return code == "model_rejected" or text.contains("模型服务拒绝了请求")

## The persona's own line for that trouble.
func model_line(r: Variant) -> String:
	var text := str(r.get("error", "")) if r is Dictionary else str(r)
	if not text.contains("模型服务拒绝了请求"):
		return NO_MODEL
	return "模型服务拒绝了：%s。" % text.get_slice("模型服务拒绝了请求：", 1).get_slice("（", 0)

## Ask, over `parent`, to open the model settings (a button that needed the model was pressed).
func remind_model(parent: Window, r: Variant = null) -> void:
	var ask := ConfirmationDialog.new()
	ask.title = "需要模型 API"
	ask.dialog_text = (model_line(r) if r != null else NO_MODEL) + "\n请到「设置 → 模型服务」填写你自己的 API（任何 OpenAI 兼容接口），测试连接后保存。"
	ask.dialog_autowrap = true
	ask.min_size = Vector2i(Vector2(420, 0) * ui_factor())
	ask.content_scale_factor = ui_factor()
	ask.ok_button_text = "去设置"
	ask.cancel_button_text = "稍后"
	ask.confirmed.connect(open_model_settings)
	ask.visibility_changed.connect(func() -> void:
		if not ask.visible:
			ask.queue_free())
	parent.add_child(ask)
	ask.popup_centered()

func open_manager() -> void:
	if _manager == null:
		_manager = PersonaManager.new()
		_manager.app = self
		_manager.theme = theme
		get_tree().root.add_child(_manager)
	show_window(_manager, PersonaManager.BASE_SIZE, PersonaManager.BASE_SIZE)
	refresh_personas()

func open_persona() -> void:
	if _persona == null:
		_persona = PersonaWindow.new()
		_persona.app = self
		_persona.theme = theme
		get_tree().root.add_child(_persona)
	show_window(_persona, PersonaWindow.BASE_SIZE, PersonaWindow.MIN_SIZE)
	_persona.reload()

## Poll a running evolution in the background so the pet can tell the owner when it is done,
## even with the persona window closed.
func watch_evolution(id: String) -> void:
	_watching = id
	if _watch.is_stopped():
		_watch.start()

func _poll_evolution() -> void:
	if _watching.is_empty():
		_watch.stop()
		return
	var r: Dictionary = await api.request_json("/v1/distillations/" + _watching)
	if not r.ok:
		if int(r.get("status", 0)) == 404:  # its persona was deleted
			_watching = ""
			_watch.stop()
		return
	evolution_updated.emit(r.data)
	if r.data.stage != "running":
		_watching = ""
		_watch.stop()
		evolution_finished.emit(r.data)

## Poll a running reflection so the pet can say when it has thought things over.
func watch_reflection(id: String) -> void:
	_watching_reflection = id
	if _reflect_watch.is_stopped():
		_reflect_watch.start()

func _poll_reflection() -> void:
	if _watching_reflection.is_empty():
		_reflect_watch.stop()
		return
	var r: Dictionary = await api.request_json("/v1/reflections/" + _watching_reflection)
	if not r.ok:
		if int(r.get("status", 0)) == 404:
			_watching_reflection = ""
			_reflect_watch.stop()
		return
	reflection_updated.emit(r.data)
	if r.data.status != "running":
		_watching_reflection = ""
		_reflect_watch.stop()
		reflection_finished.emit(r.data)

# ---- interface size ------------------------------------------------------------------------

## The screen the pet stands on; windows open there.
func screen() -> int:
	return get_tree().root.current_screen

## Pixels per layout unit on a screen: its HiDPI scale times the chosen interface size.
func ui_factor(on := screen()) -> float:
	return DisplayServer.screen_get_scale(on) * zoom

## Open a window scaled for the pet's screen, centred on that screen's work area. `base` is
## the layout size in units; the factor shrinks if the window would not fit the screen.
func show_window(win: Window, base: Vector2i, min_base: Vector2i) -> void:
	var on := screen()
	var area := DisplayServer.screen_get_usable_rect(on)
	var f := minf(ui_factor(on), minf(area.size.x * 0.94 / base.x, area.size.y * 0.94 / base.y))
	win.content_scale_factor = f
	# Only the root window picks up the project's nearest filter; keep pixel art crisp here too.
	win.canvas_item_default_texture_filter = Viewport.DEFAULT_CANVAS_ITEM_TEXTURE_FILTER_NEAREST
	win.min_size = Vector2i(Vector2(min_base) * f)
	var sz := Vector2i(Vector2(base) * f)
	win.always_on_top = false  # popup() makes it transient, which a window on top cannot be
	win.popup(Rect2i(area.position + (area.size - sz) / 2, sz))
	# popup() makes it transient, and on macOS a transient window is a child of the pet's window,
	# dragged along whenever the pet walks or is moved.
	win.transient = false
	bring_forward(win)
	win.grab_focus()

## Raise a window when requested without keeping it above unrelated applications.
func bring_forward(win: Window) -> void:
	win.always_on_top = false
	DisplayServer.window_move_to_foreground(win.get_window_id())

## Pop a dialog owned by `owner_win` at the owner's scale (dialogs size themselves unscaled).
func popup_dialog(dlg: Window, base: Vector2i, owner_win: Window) -> void:
	dlg.content_scale_factor = owner_win.content_scale_factor
	dlg.popup_centered(Vector2i(Vector2(base) * owner_win.content_scale_factor))

## Right-click menus ignore content_scale_factor, so enlarge their fonts, padding and
## check marks directly.
func style_menu(menu: PopupMenu, f := ui_factor()) -> void:
	menu.add_theme_font_size_override("font_size", roundi(16 * f))
	menu.add_theme_font_size_override("font_separator_size", roundi(13 * f))
	menu.add_theme_constant_override("v_separation", roundi(12 * f))
	menu.add_theme_constant_override("h_separation", roundi(8 * f))
	menu.add_theme_constant_override("item_start_padding", roundi(12 * f))
	menu.add_theme_constant_override("item_end_padding", roundi(16 * f))
	var panel := _box(UI.C_SURFACE, UI.C_FRAME, roundi(6 * f))
	panel.set_border_width_all(maxi(2, roundi(2 * f)))
	menu.add_theme_stylebox_override("panel", panel)
	var hover := _box(UI.C_BTN_HOVER, Color(0, 0, 0, 0), 0)
	hover.set_corner_radius_all(roundi(3 * f))
	menu.add_theme_stylebox_override("hover", hover)
	var px := roundi(14 * f)
	menu.add_theme_icon_override("checked", _check_icon(px, true))
	menu.add_theme_icon_override("unchecked", _check_icon(px, false))
	menu.add_theme_icon_override("radio_checked", _check_icon(px, true, true))
	menu.add_theme_icon_override("radio_unchecked", _check_icon(px, false, true))
	menu.add_theme_icon_override("submenu", _arrow_icon(px))

func _check_icon(px: int, on: bool, round_mark := false) -> ImageTexture:
	var img := Image.create(px, px, false, Image.FORMAT_RGBA8)
	var w := maxi(1, px / 7)
	var c := Vector2(px, px) / 2.0
	for y in px:
		for x in px:
			var p := Vector2(x + 0.5, y + 0.5)
			var edge: bool
			var inside: bool
			if round_mark:
				var d := p.distance_to(c)
				edge = d <= px / 2.0 and d > px / 2.0 - w
				inside = d <= px / 2.0 - w * 2
			else:
				edge = x < w or y < w or x >= px - w or y >= px - w
				inside = x >= w * 2 and y >= w * 2 and x < px - w * 2 and y < px - w * 2
			if edge:
				img.set_pixel(x, y, UI.C_FRAME)
			elif inside and on:
				img.set_pixel(x, y, UI.C_PRIMARY)
			else:
				img.set_pixel(x, y, UI.C_INPUT)
	return ImageTexture.create_from_image(img)

func _arrow_icon(px: int) -> ImageTexture:
	var img := Image.create(px, px, false, Image.FORMAT_RGBA8)
	for y in px:
		var half := minf(y + 0.5, px - y - 0.5)
		for x in px:
			if x >= px * 0.3 and x - px * 0.3 < half * 0.9:
				img.set_pixel(x, y, UI.C_TEXT)
	return ImageTexture.create_from_image(img)

# ---- theme (from ustc-discussion-student, extended for the workbench's controls) ----------

func _build_theme() -> Theme:
	var t := Theme.new()
	t.default_font = UI.body_font()
	t.default_font_size = 15
	t.set_color("font_color", "Label", UI.C_TEXT)
	for kind in ["Button", "LineEdit", "OptionButton", "CheckBox", "TextEdit", "ItemList", "Tree",
			"PopupMenu", "RichTextLabel"]:
		t.set_color("font_color", kind, UI.C_TEXT)
		t.set_color("font_hover_color", kind, UI.C_TEXT)
		t.set_color("font_pressed_color", kind, UI.C_TEXT)
		t.set_color("font_focus_color", kind, UI.C_TEXT)
		t.set_color("font_selected_color", kind, UI.C_TEXT)
	t.set_color("default_color", "RichTextLabel", UI.C_TEXT)
	t.set_color("font_disabled_color", "Button", UI.C_MUTED)
	t.set_color("font_disabled_color", "PopupMenu", UI.C_MUTED)
	t.set_color("font_placeholder_color", "LineEdit", UI.C_MUTED)
	t.set_color("font_placeholder_color", "TextEdit", UI.C_MUTED)
	t.set_color("caret_color", "LineEdit", UI.C_TEXT)
	t.set_color("caret_color", "TextEdit", UI.C_TEXT)
	t.set_color("selection_color", "TextEdit", UI.C_BTN_PRESSED)
	t.set_color("selection_color", "LineEdit", UI.C_BTN_PRESSED)
	t.set_color("selection_color", "RichTextLabel", UI.C_BTN_PRESSED)
	t.set_color("font_hover_color", "PopupMenu", UI.C_TEXT)
	t.set_color("font_separator_color", "PopupMenu", UI.C_MUTED)
	t.set_color("guide_color", "Tree", Color(UI.C_FRAME, 0.25))
	t.set_color("guide_color", "ItemList", Color(UI.C_FRAME, 0.25))
	var edge := UI.C_FRAME
	t.set_stylebox("normal", "Button", _box(UI.C_SURFACE_2, edge))
	t.set_stylebox("hover", "Button", _box(UI.C_BTN_HOVER, edge))
	t.set_stylebox("pressed", "Button", _box(UI.C_BTN_PRESSED, edge))
	t.set_stylebox("disabled", "Button", _box(UI.C_SURFACE_2.darkened(0.15), edge))
	t.set_stylebox("focus", "Button", _box(Color(0, 0, 0, 0), UI.C_PRIMARY))
	for st in ["normal", "hover", "pressed", "disabled", "focus"]:
		t.set_stylebox(st, "OptionButton", t.get_stylebox(st, "Button"))
	t.set_stylebox("normal", "LineEdit", _box(UI.C_INPUT, UI.C_FRAME))
	t.set_stylebox("focus", "LineEdit", _box(Color(0, 0, 0, 0), UI.C_PRIMARY))
	t.set_stylebox("normal", "TextEdit", _box(UI.C_INPUT, UI.C_FRAME))
	t.set_stylebox("focus", "TextEdit", _box(Color(0, 0, 0, 0), UI.C_PRIMARY))
	t.set_stylebox("panel", "PanelContainer", _box(UI.C_SURFACE, UI.C_FRAME, 10))
	t.set_stylebox("panel", "Panel", _box(UI.C_SURFACE, UI.C_FRAME, 0))
	t.set_stylebox("panel", "ItemList", _box(UI.C_INPUT, UI.C_FRAME, 4))
	t.set_stylebox("focus", "ItemList", _box(Color(0, 0, 0, 0), Color(0, 0, 0, 0)))
	t.set_stylebox("selected", "ItemList", _box(UI.C_BTN_HOVER, Color(0, 0, 0, 0), 2))
	t.set_stylebox("selected_focus", "ItemList", _box(UI.C_BTN_HOVER, Color(0, 0, 0, 0), 2))
	t.set_stylebox("hovered", "ItemList", _box(UI.C_MINE_BG, Color(0, 0, 0, 0), 2))
	t.set_stylebox("panel", "Tree", _box(UI.C_INPUT, UI.C_FRAME, 4))
	t.set_stylebox("focus", "Tree", _box(Color(0, 0, 0, 0), Color(0, 0, 0, 0)))
	t.set_stylebox("selected", "Tree", _box(UI.C_BTN_HOVER, Color(0, 0, 0, 0), 2))
	t.set_stylebox("selected_focus", "Tree", _box(UI.C_BTN_HOVER, Color(0, 0, 0, 0), 2))
	t.set_stylebox("panel", "PopupMenu", _box(UI.C_SURFACE, UI.C_FRAME, 6))
	t.set_stylebox("hover", "PopupMenu", _box(UI.C_BTN_HOVER, Color(0, 0, 0, 0), 2))
	t.set_stylebox("background", "ProgressBar", _box(UI.C_INPUT, UI.C_FRAME, 0))
	t.set_stylebox("fill", "ProgressBar", _box(Color("#6a9a4c"), Color(0, 0, 0, 0), 0))
	t.set_color("font_color", "ProgressBar", UI.C_TEXT)
	t.set_stylebox("normal", "RichTextLabel", _box(UI.C_INPUT, UI.C_FRAME, 8))
	t.set_stylebox("split_bar_background", "HSplitContainer", StyleBoxEmpty.new())
	t.set_stylebox("panel", "TabContainer", StyleBoxEmpty.new())
	t.set_stylebox("tab_selected", "TabContainer", _box(UI.C_SURFACE, UI.C_FRAME, 6))
	t.set_stylebox("tab_unselected", "TabContainer", _box(UI.C_SURFACE_2, Color(UI.C_FRAME, 0.5), 6))
	t.set_stylebox("tab_hovered", "TabContainer", _box(UI.C_BTN_HOVER, Color(UI.C_FRAME, 0.5), 6))
	t.set_stylebox("tab_focus", "TabContainer", StyleBoxEmpty.new())
	t.set_stylebox("tabbar_background", "TabContainer", StyleBoxEmpty.new())
	t.set_color("font_selected_color", "TabContainer", UI.C_TEXT)
	t.set_color("font_unselected_color", "TabContainer", UI.C_MUTED)
	t.set_color("font_hovered_color", "TabContainer", UI.C_TEXT)
	t.set_constant("side_margin", "TabContainer", 0)
	return t

func _box(bg: Color, border := Color(0, 0, 0, 0), pad := 8) -> StyleBoxFlat:
	var b := StyleBoxFlat.new()
	b.bg_color = bg
	b.set_corner_radius_all(2)
	if border.a > 0:
		b.set_border_width_all(UI.FRAME_W)
		b.border_color = border
	b.content_margin_left = pad + 4
	b.content_margin_right = pad + 4
	b.content_margin_top = pad
	b.content_margin_bottom = pad
	return b
