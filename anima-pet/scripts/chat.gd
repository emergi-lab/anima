class_name PetChat
extends Node
## Chat with the persona: the input bar beside the pet, the bubble over its head, and the calls
## to anima-server (`chat:say`, `chat:end`). The server keeps the session and its memories; this
## only shows them. Emotions from the server pick the emote the pet wears while it speaks.

## Server emotion → [word shown in the log, PIPOYA emote].
const EMOTIONS := {
	"calm": ["平静", "talk"], "happy": ["开心", "music"], "excited": ["兴奋", "star"],
	"surprised": ["惊讶", "shock"], "curious": ["好奇", "question"],
	"confused": ["困惑", "swirl"], "thoughtful": ["思考", "think"],
	"agreeing": ["赞同", "agree"], "embarrassed": ["不好意思", "blush"],
	"worried": ["担心", "sweat"], "sad": ["难过", "cry"], "angry": ["生气", "angry"],
	"inspired": ["灵光一闪", "idea"],
}
const INPUT_GAP := 6  # screen pixels between the pet's sprite and the input bar
const HINT_SESSIONS := 5  # chats since the current version before the reflection reminder

## The App autoload and the pet, injected on creation.
var app: Node
var pet: Node2D
var bubble: ChatBubble
var input: ChatInput
var window: ChatWindow  # the chat as a window (⤢); while it is open the head bubble rests
var waiting := false
var _tip := Vector2i.ZERO  # where the bubble's tail points; fixed until the next line
var _area := Rect2i()

func _ready() -> void:
	bubble = ChatBubble.new()
	bubble.app = app
	bubble.theme = app.theme
	add_child(bubble)
	input = ChatInput.new()
	input.app = app
	input.theme = app.theme
	add_child(input)
	input.sent.connect(say)
	input.log_requested.connect(app.open_chat_log)
	input.expand_requested.connect(expand)
	window = ChatWindow.new()
	window.app = app
	window.theme = app.theme
	add_child(window)
	window.sent.connect(say)
	window.collapsed.connect(func(text: String) -> void:
		open()
		input._line.text = text
		input._line.caret_column = text.length())
	bubble.typing_changed.connect(func(on: bool) -> void: pet.set_talking(on))
	bubble.reshaped.connect(func() -> void: bubble.place(_tip, _area))
	bubble.finished.connect(func() -> void: pet.clear_emote())

## Chat windows are up: the pet stays put and faces the owner.
func active() -> bool:
	return input.visible or bubble.visible or window.visible

func open() -> void:
	if app.persona_id.is_empty():
		return
	if window.visible:  # chatting in the window: that is where to type
		window.bring_up()
		return
	var f: float = app.ui_factor()
	input.content_scale_factor = f
	input.size = Vector2i(Vector2(ChatInput.BASE_SIZE) * f)
	bubble.content_scale_factor = f
	place()
	input.open_for(app.persona_name)
	input.set_waiting(waiting, app.persona_name)

func close() -> void:
	input.close()

## From the input bar to the chat window, keeping what was typed.
func expand(text: String) -> void:
	input.close()
	bubble.dismiss()
	window.set_waiting(waiting)
	await window.open_with(text)

## Put the bubble over the pet's head and the input bar beside it (right if there is room).
## Done when they appear, not every frame: they stay where they are if the pet moves.
func place() -> void:
	_anchor_bubble()
	var win := pet.get_window()
	var area := _area
	var sprite: Rect2 = pet.sprite_rect()
	var right := win.position.x + roundi(sprite.end.x) + INPUT_GAP
	var x := right if right + input.size.x <= area.end.x \
		else win.position.x + roundi(sprite.position.x) - INPUT_GAP - input.size.x
	var y := win.position.y + roundi(sprite.get_center().y) - input.size.y / 2
	input.position = Vector2i(clampi(x, area.position.x, area.end.x - input.size.x),
		clampi(y, area.position.y, area.end.y - input.size.y))

func _anchor_bubble() -> void:
	var win := pet.get_window()
	_area = DisplayServer.screen_get_usable_rect(win.current_screen)
	var sprite: Rect2 = pet.sprite_rect()
	_tip = win.position + Vector2i(roundi(sprite.get_center().x), roundi(pet.head_top()))
	bubble.place(_tip, _area)

func say(text: String) -> void:
	if waiting or app.persona_id.is_empty():
		return
	waiting = true
	var windowed := window.visible
	input.set_waiting(true, app.persona_name)
	window.set_waiting(true)
	if windowed:
		window.add_owner(text)
	else:
		_anchor_bubble()
		bubble.think()
	pet.hold_emote("think")
	var r: Dictionary = await app.api.request_json("/v1/personas/%s/chat:say" % app.persona_id.uri_encode(),
		HTTPClient.METHOD_POST, {"text": text, "persona_name": app.persona_name})
	waiting = false
	input.set_waiting(false, app.persona_name)
	window.set_waiting(false)
	if not r.ok and app.model_trouble(r):
		pet.hold_emote("sweat")
		if windowed:
			window.add_note(app.model_line(r), app.open_model_settings)
			window.restore(text)
		else:
			_anchor_bubble()
			bubble.link(app.model_line(r), app.open_model_settings)
			input.restore(text)
		return
	if not r.ok:
		pet.hold_emote("sweat")
		if windowed:
			window.add_note(_trouble(r))
		else:
			bubble.note(_trouble(r))
		return
	var reply: Dictionary = r.data.reply
	_maybe_hint()
	var feel = reply.get("emotion")
	pet.hold_emote(EMOTIONS[feel][1] if feel in EMOTIONS else "talk")
	if windowed:  # the reply types out in the window; the pet bobs along, no head bubble
		window.add_reply(reply)
		pet.set_talking(true)
		var typing := str(reply.text).length() * ChatTimeline.CHAR_SECONDS
		await get_tree().create_timer(typing).timeout
		pet.set_talking(false)
		await get_tree().create_timer(ChatBubble.READ_MIN).timeout
		if not waiting:
			pet.clear_emote()
	else:
		bubble.say(str(reply.text))

## With the reminder on: once per version, after HINT_SESSIONS chats, suggest a reflection —
## after this reply has been read.
func _maybe_hint() -> void:
	if not app.reflect_hint or app.persona_id.is_empty():
		return
	var r: Dictionary = await app.api.request_json("/v1/personas/%s/reflection:preview" % app.persona_id.uri_encode())
	if not r.ok or r.data.get("open") != null or int(r.data.sessions) < HINT_SESSIONS \
			or not bool(r.data.enough):
		return
	var key := "%s|B%d" % [app.persona_id, int(r.data.since_version)]
	if app.hinted(key):
		return
	app.mark_hinted(key)
	await get_tree().create_timer(12.0).timeout
	if not waiting:
		announce("我们聊了好些了，要不要我反思一下最近的事？右键「人格」→「反思」。", "think")

## Say something unprompted (an evolution finished, the persona changed).
func announce(text: String, emote: String) -> void:
	if waiting:
		return
	bubble.content_scale_factor = app.ui_factor()
	pet.hold_emote(emote)
	_anchor_bubble()
	bubble.say(text)

## No key yet: say so once, as a line that opens the settings when clicked.
func remind_model() -> void:
	if waiting:
		return
	bubble.content_scale_factor = app.ui_factor()
	pet.hold_emote("sweat")
	_anchor_bubble()
	bubble.link(app.NO_MODEL, app.open_model_settings)

## End the server session so its memories form now (quitting); quick, and failures are fine.
func end_session() -> void:
	if app.persona_id.is_empty():
		return
	await app.api.request_json("/v1/personas/%s/chat:end" % app.persona_id.uri_encode(),
		HTTPClient.METHOD_POST, {}, 3.0)

func _trouble(r: Dictionary) -> String:
	match int(r.get("status", 0)):
		0:
			return "连不上 anima-server，先把后端启动起来再聊吧。"
		404:
			return "找不到这个人格了，可能要在蒸馏工作台重新导入。"
		502:
			return "这句我没想好怎么接……换个说法试试？"
		503:
			return "模型服务暂时连不上，等一会儿再说吧。"
	return str(r.error)
