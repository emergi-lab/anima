extends Node2D
## The desktop pet: a transparent, borderless window holding the persona's
## PIPOYA avatar. It idles, wanders along the bottom of the screen (optional), reacts to
## clicks and drags, and opens the other windows from its right-click menu. Clicks outside
## the sprite pass through to the desktop.

enum Mode { IDLE, WALK, DRAG, FALL }

## Sprite pixels per art pixel per unit of App.ui_factor (4 on a 1x screen at the standard size).
const SCALE_PER_FACTOR := 3.2
## Window size at sprite scale 4; it grows in proportion with the sprite.
const BASE_WINDOW := Vector2(200, 250)  # room above the head for the chat bubble's tail
const WALK_SPEED := 46.0  # px per second
const FALL_SPEED := 900.0
const CLICK_PX := 4  # a press that moves less than this is a click, not a drag
const REACTIONS := ["sparkle", "agree", "idea"]

enum Menu { TALK, LOG, PERSONA, LOOK, WORKBENCH, SETTINGS, QUIT }
const SWITCH_ID := 200  # persona items are SWITCH_ID + index into App.personas
const MANAGE_ID := 299

var _mode := Mode.IDLE
var _timer := 1.5  # seconds left in the current idle or walk spell
var _dir := 1
var _x := 0.0  # window x as float so slow walking accumulates
var _y := 0.0
var _press := Vector2i.ZERO
var _drag_offset := Vector2i.ZERO
var _pressed := false
var _emote_left := 0.0
var _screen_check := 0.0  # seconds until the next "still on a screen?" look
var _scale := 4  # current sprite scale
@onready var _avatar: AvatarView = $Avatar
var _tail := Node2D.new()  # the chat bubble's tail (_draw_tail)
var _tail_shown := false
@onready var _menu: PopupMenu = $Menu
var _wandered := true  # wander as last applied: turning it on drops the pet to the ground
var _switch_menu := PopupMenu.new()
var _chat: PetChat

func _ready() -> void:
	var win := get_window()
	win.transparent_bg = true
	_apply_look()
	App.look_changed.connect(_apply_look)
	App.settings_changed.connect(_apply_settings)
	_apply_scale()
	var area := _usable()
	_x = area.end.x - win.size.x - 160
	_y = _ground()
	win.position = Vector2i(roundi(_x), roundi(_y))
	_menu.theme = App.theme
	get_tree().auto_accept_quit = false  # end the chat session first (_quit)
	_chat = PetChat.new()
	_chat.app = App
	_chat.pet = self
	add_child(_chat)
	_tail.draw.connect(_draw_tail)
	add_child(_tail)
	_menu.add_item("对话…", Menu.TALK)
	_menu.add_item("聊天记录…", Menu.LOG)
	_menu.add_item("人格…", Menu.PERSONA)
	_switch_menu.name = "Switch"
	_switch_menu.theme = App.theme
	_switch_menu.id_pressed.connect(_on_menu)
	_menu.add_child(_switch_menu)
	_menu.add_submenu_node_item("切换人格", _switch_menu)
	_menu.add_separator()
	_menu.add_item("换形象…", Menu.LOOK)
	_menu.add_item("蒸馏工作台…", Menu.WORKBENCH)
	_menu.add_separator()
	_menu.add_item("设置…", Menu.SETTINGS)
	_menu.add_separator()
	_menu.add_item("退出", Menu.QUIT)
	_menu.id_pressed.connect(_on_menu)
	_apply_settings()
	App.evolution_finished.connect(_on_evolution_finished)
	App.reflection_finished.connect(_on_reflection_finished)
	App.talk_requested.connect(func() -> void: _on_menu(Menu.TALK))
	App.persona_switching.connect(func() -> void:
		_chat.close()
		_chat.bubble.dismiss())
	App.persona_switched.connect(func() -> void:
		react("sparkle")
		if App.persona_id.is_empty():
			_chat.window.hide()
		elif _chat.window.visible:
			_chat.window.reload())
	App.personas_changed.connect(func() -> void:
		_apply_settings()
		_fill_switch_menu())
	App.refresh_personas()
	App.persona_changed.connect(func(line: String) -> void: _chat.announce(line, "sparkle"))
	App.model_ready_changed.connect(func() -> void:
		if App.model_ready:
			_chat.announce("好啦，我能思考了！", "music"))
	_remind_model()

## Each start while no API key is set: say so once, as a line that opens the settings.
func _remind_model() -> void:
	await App.check_model()
	if not App.model_ready:
		await get_tree().create_timer(1.5).timeout
		_chat.remind_model()

## A reflection is thought through: say so; nothing changes until the owner approves.
func _on_reflection_finished(d: Dictionary) -> void:
	match str(d.status):
		"ready":
			if d.versions.is_empty():
				_chat.announce("想了想最近的事，我还是我。", "think")
			else:
				_chat.announce("我想了想最近的事……来「人格」→「反思」看看，你来定。", "idea")
		"failed":
			_chat.announce("刚才想事情想岔了……再试一次吧。", "sweat")

## The evolution job ended: say so over the pet's head.
func _on_evolution_finished(d: Dictionary) -> void:
	match str(d.stage):
		"review":
			if int(d.progress.entries) > 0:
				_chat.announce("我好像想明白了些事……来「人格」里看看？", "idea")
			else:
				_chat.announce("这阵子我好像没什么变化。", "think")
		"failed":
			_chat.announce("刚才想事情想岔了……再试一次吧。", "sweat")

func _apply_look() -> void:
	_avatar.seed_text = App.persona_id
	_avatar.spec = App.look()
	_avatar.nickname = App.persona_name

func _apply_settings() -> void:
	_avatar.show_name = App.show_name
	_apply_scale()
	var none: bool = App.persona_id.is_empty()
	for id in [Menu.TALK, Menu.LOG, Menu.PERSONA]:
		_menu.set_item_disabled(_menu.get_item_index(id), none)
	_menu.set_item_text(_menu.get_item_index(Menu.TALK),
		"和%s说话%s" % [App.persona_name, "（先导入人格）" if none else "…"])
	if not App.wander and _mode == Mode.WALK:
		_idle()
	elif App.wander and not _wandered:
		_mode = Mode.FALL  # back to the bottom edge to walk along it
	_wandered = App.wander
	_update_passthrough()

## Size the sprite (and the window around it) for the interface size and the screen's HiDPI
## scale. Integer pixel scales keep the art crisp. The feet stay where they were.
func _apply_scale() -> void:
	var win := get_window()
	_scale = maxi(2, roundi(SCALE_PER_FACTOR * App.ui_factor()))
	_avatar.pixel_scale = _scale
	_avatar.name_size = roundi(15.0 * _scale / 4.0)
	var feet := win.position.y + win.size.y
	win.size = Vector2i(BASE_WINDOW * _scale / 4.0)
	_avatar.position = Vector2((win.size.x - _avatar.size.x) / 2.0, win.size.y - _avatar.size.y)
	if is_node_ready() and _mode != Mode.DRAG:
		_y = minf(float(feet - win.size.y), _ground())
		win.position = Vector2i(roundi(_x), roundi(_y))
	_update_passthrough()

## Work area of the screen the pet is on (excludes the menu bar and the Dock).
func _usable() -> Rect2i:
	return DisplayServer.screen_get_usable_rect(get_window().current_screen)

## Window y at which the sprite's feet rest on the bottom of the work area.
func _ground() -> float:
	# The sprite can overlap the floor slightly; the name tag must stay above it.
	return float(_usable().end.y - get_window().size.y + (0 if App.show_name else _scale * 2))

func sprite_rect() -> Rect2:
	var sw := float(Avatar.FRAME * _scale)
	return Rect2(_avatar.position + Vector2((_avatar.size.x - sw) / 2.0, _avatar.head_room()), Vector2(sw, sw))

## The chat bubble's tail, from under the bubble down to the head (see chat_bubble.gd). Not
## drawn once the pet has been dragged from under the bubble.
func _draw_tail() -> void:
	_tail_shown = false
	var b: ChatBubble = _chat.bubble
	if not b.visible:
		return
	var f := b.content_scale_factor
	var origin := Vector2(b.position - get_window().position)
	var tip := origin + Vector2(b.tail()[0][2]) * f
	var sprite := sprite_rect()
	if absf(tip.y - head_top()) > 3 * f or tip.x < sprite.position.x or tip.x > sprite.end.x:
		return
	_tail_shown = true
	for i in 2:
		var pts := PackedVector2Array()
		for p in b.tail()[i]:
			pts.append(origin + p * f)
		_tail.draw_colored_polygon(pts, Color(UI.C_FRAME if i == 0 else UI.C_BUBBLE, b.alpha()))

## Window y where the emote above the head ends; the chat bubble's tail points here.
func head_top() -> float:
	return _avatar.position.y

## Windows also clips drawing outside this polygon, so include a visible name tag.
## Other platforms can keep the tag click-through without hiding it.
func _update_passthrough() -> void:
	var r := sprite_rect().grow(-_scale * 4)
	if OS.get_name() == "Windows" and _avatar.show_name:
		var top := sprite_rect().end.y
		var tag := Rect2(Vector2(_avatar.position.x, top),
			Vector2(_avatar.size.x, _avatar.position.y + _avatar.size.y - top))
		r = r.merge(tag)
	DisplayServer.window_set_mouse_passthrough(PackedVector2Array([
		r.position, Vector2(r.end.x, r.position.y), r.end, Vector2(r.position.x, r.end.y)]))

# ---- behaviour ----------------------------------------------------------------------------

## Back onto a screen if the pet ended up off every screen's work area (a display was unplugged
## or rearranged): onto the screen below/above it, else the main one, standing on its bottom edge.
func _keep_on_screen(delta: float) -> void:
	_screen_check -= delta
	if _screen_check > 0.0 or _mode == Mode.DRAG:
		return
	_screen_check = 1.0
	var win := get_window()
	var center := win.position + Vector2i(sprite_rect().get_center())
	var target := DisplayServer.get_primary_screen()
	for i in DisplayServer.get_screen_count():
		var area := DisplayServer.screen_get_usable_rect(i)
		if area.has_point(center):
			return
		if center.x >= area.position.x and center.x < area.end.x:
			target = i
	var area := DisplayServer.screen_get_usable_rect(target)
	_x = clampf(float(win.position.x), float(area.position.x), float(area.end.x - win.size.x))
	_y = float(area.end.y - win.size.y + (0 if App.show_name else _scale * 2))
	win.position = Vector2i(roundi(_x), roundi(_y))
	_idle()
	_apply_scale()  # the screen may have another HiDPI scale

func _process(delta: float) -> void:
	_keep_on_screen(delta)
	if _chat.bubble.visible or _tail_shown:
		_tail.queue_redraw()
	if _emote_left > 0.0:
		_emote_left -= delta
		if _emote_left <= 0.0 and _mode != Mode.DRAG:
			_avatar.emote = ""
	if _chat.active() and _mode == Mode.WALK:
		_idle()
	match _mode:
		Mode.IDLE:
			_timer -= delta
			if _timer <= 0.0:
				if App.wander and not _chat.active() and randf() < 0.6:
					_walk()
				else:
					_glance()
		Mode.WALK:
			_timer -= delta
			_x += _dir * WALK_SPEED * delta
			var area := _usable()
			var lo := float(area.position.x)
			var hi := float(area.end.x - get_window().size.x)
			if _x <= lo or _x >= hi:
				_x = clampf(_x, lo, hi)
				_dir = -_dir
				_avatar.facing = Avatar.Facing.RIGHT if _dir > 0 else Avatar.Facing.LEFT
			get_window().position = Vector2i(roundi(_x), roundi(_y))
			if _timer <= 0.0:
				_idle()
		Mode.FALL:
			_y = minf(_y + FALL_SPEED * delta, _ground())
			get_window().position = Vector2i(roundi(_x), roundi(_y))
			if _y >= _ground():
				_idle()

func _idle() -> void:
	_mode = Mode.IDLE
	_timer = randf_range(3.0, 8.0)
	_avatar.walking = false
	_avatar.facing = Avatar.Facing.DOWN

func _walk() -> void:
	_mode = Mode.WALK
	_timer = randf_range(2.0, 6.0)
	_dir = -1 if randf() < 0.5 else 1
	_avatar.facing = Avatar.Facing.RIGHT if _dir > 0 else Avatar.Facing.LEFT
	_avatar.walking = true

## Look left or right for a moment, then face the screen again.
func _glance() -> void:
	_avatar.facing = [Avatar.Facing.LEFT, Avatar.Facing.RIGHT][randi() % 2]
	_timer = randf_range(1.0, 2.0)
	await get_tree().create_timer(_timer * 0.8).timeout
	if _mode == Mode.IDLE:
		_avatar.facing = Avatar.Facing.DOWN
		_timer = randf_range(3.0, 8.0)

func react(emote: String, seconds := 1.6) -> void:
	_avatar.emote = emote
	_emote_left = seconds

## Wear an emote until clear_emote() (while a chat answer is coming or being said).
func hold_emote(emote: String) -> void:
	react(emote, INF)

func clear_emote() -> void:
	_emote_left = 0.0
	if _mode != Mode.DRAG:
		_avatar.emote = ""

func set_talking(on: bool) -> void:
	_avatar.talking = on

# ---- input --------------------------------------------------------------------------------

func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT:
		if event.pressed and event.double_click:
			_pressed = false
			_on_menu(Menu.TALK)
		elif event.pressed:
			_pressed = true
			_press = DisplayServer.mouse_get_position()
			_drag_offset = _press - get_window().position
		elif _pressed:
			_pressed = false
			if _mode == Mode.DRAG:
				_drop()
			else:
				react(REACTIONS[randi() % REACTIONS.size()])
	elif event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_RIGHT and event.pressed:
		_apply_settings()
		_fill_switch_menu()
		App.refresh_personas()  # for the next time; the list rarely changes in between
		var f := App.ui_factor()
		App.style_menu(_menu, f)
		App.style_menu(_switch_menu, f)
		_menu.reset_size()
		# Open upward from the cursor (the pet stands on the bottom edge), kept on screen.
		var area := _usable()
		var at := DisplayServer.mouse_get_position() - Vector2i(0, _menu.size.y)
		_menu.position = at.clamp(area.position, area.end - _menu.size)
		_menu.popup()
	elif event is InputEventMouseMotion and _pressed:
		var mouse := DisplayServer.mouse_get_position()
		if _mode != Mode.DRAG and (mouse - _press).length() >= CLICK_PX:
			_mode = Mode.DRAG
			_avatar.walking = false
			_avatar.facing = Avatar.Facing.DOWN
			_avatar.emote = "exclaim"
		if _mode == Mode.DRAG:
			var p := mouse - _drag_offset
			_x = p.x
			_y = p.y
			get_window().position = p

## Released after a drag: fall back to the bottom when wandering, otherwise stay put.
func _drop() -> void:
	react("sweat", 1.2)
	if App.wander:
		_mode = Mode.FALL
	else:
		_idle()
	_apply_scale()  # it may have been dropped on another screen

func _on_menu(id: int) -> void:
	App.note("菜单 %d" % id)
	match id:
		Menu.TALK:
			if not App.persona_id.is_empty():
				_idle()
				_chat.open()
		Menu.LOG:
			App.open_chat_log()
		Menu.PERSONA:
			App.open_persona()
		Menu.LOOK:
			App.open_picker()
		Menu.WORKBENCH:
			App.open_workbench()
		Menu.SETTINGS:
			App.open_settings()
		Menu.QUIT:
			_quit()
		MANAGE_ID:
			App.open_manager()
		_ when id >= SWITCH_ID and id - SWITCH_ID < App.personas.size():
			var p: Dictionary = App.personas[id - SWITCH_ID]
			_idle()
			App.switch_persona(str(p.persona_id), str(p.name))

## One radio item per persona (its icon, name, version, last chat), then "管理人格…".
## Switching waits while an answer is on its way.
func _fill_switch_menu() -> void:
	_switch_menu.clear()
	var f := App.ui_factor()
	for i in App.personas.size():
		var p: Dictionary = App.personas[i]
		var label := "%s    B%d · %s" % [p.name, int(p.k), App.ago(p.last_chat_at)]
		_switch_menu.add_radio_check_item(label, SWITCH_ID + i)
		var at := _switch_menu.get_item_index(SWITCH_ID + i)
		_switch_menu.set_item_icon(at, App.persona_icon(str(p.persona_id), 2))
		_switch_menu.set_item_icon_max_width(at, roundi(28 * f))
		_switch_menu.set_item_checked(at, str(p.persona_id) == App.persona_id)
		_switch_menu.set_item_disabled(at, _chat.waiting)
	if not App.personas.is_empty():
		_switch_menu.add_separator()
	_switch_menu.add_item("管理人格…", MANAGE_ID)

func _notification(what: int) -> void:
	if what == NOTIFICATION_WM_CLOSE_REQUEST:
		_quit()

## End the chat session (so it becomes a memory now), then quit.
func _quit() -> void:
	_chat.close()
	await _chat.end_session()
	get_tree().quit()
