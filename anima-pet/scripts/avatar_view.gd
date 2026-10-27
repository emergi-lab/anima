class_name AvatarView
extends Control
## Draws one layered Avatar at an integer scale: walk cycle, talking bob, an optional
## PIPOYA popup emote over the head and an optional name tag under the feet.
## Adapted from ustc-discussion-student (group marks removed, walk frames added).
## A null spec falls back to the default look for seed_text.

var spec = null:
	set(v):
		spec = v
		_resolve()
var seed_text := "":
	set(v):
		seed_text = v
		_resolve()
var facing := Avatar.Facing.DOWN:
	set(v):
		facing = v
		queue_redraw()
var nickname := "":
	set(v):
		nickname = v
		queue_redraw()
var pixel_scale := 3:
	set(v):
		pixel_scale = v
		_update_size()
var show_name := true:
	set(v):
		show_name = v
		_update_size()
## Font size of the name tag; the pet enlarges it with the interface.
var name_size := 13:
	set(v):
		name_size = v
		_update_size()
var talking := false:
	set(v):
		talking = v
		queue_redraw()
## Steps through the three walk frames of the current facing.
var walking := false:
	set(v):
		walking = v
		queue_redraw()
## Popup emote key from the manifest ("talk", "idea", "think", …); empty hides it.
var emote := "":
	set(v):
		if emote != v:
			emote = v
			_emote_t = 0.0
			queue_redraw()

var _layers := []
var _t := 0.0
var _emote_t := 0.0
var _bob := 0.0
var _step := 1
var _emote_frame := 0

func _ready() -> void:
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	_resolve()
	_update_size()

func resolved_spec() -> Dictionary:
	return Avatar.normalize(spec, seed_text)

func _resolve() -> void:
	_layers = Avatar.layers(resolved_spec())
	queue_redraw()

## Room above the sprite for the emote, below it for the name tag.
func head_room() -> float:
	return Avatar.FRAME * pixel_scale * 0.45

func _update_size() -> void:
	custom_minimum_size = Vector2(maxi(Avatar.FRAME * pixel_scale, name_size * 5 if show_name else 0),
		head_room() + Avatar.FRAME * pixel_scale + (name_size * 1.5 if show_name else 0.0) + 4)
	size = custom_minimum_size
	queue_redraw()

func _process(delta: float) -> void:
	_t += delta
	_emote_t += delta
	var bob := (-1.0 if int(_t / 0.3) % 2 == 0 else 0.0) if talking else 0.0
	var step: int = [0, 1, 2, 1][int(_t / 0.16) % 4] if walking else 1
	var frame: int = [0, 1, 2, 1][int(_emote_t / 0.18) % 4]
	if bob != _bob or step != _step or (not emote.is_empty() and frame != _emote_frame):
		queue_redraw()
	_bob = bob
	_step = step
	_emote_frame = frame

func _draw() -> void:
	var sw := Avatar.FRAME * pixel_scale
	var x := (size.x - sw) / 2.0
	var y := head_room() + _bob * pixel_scale
	var src := Avatar.region(facing, _step)
	for l in _layers:
		draw_texture_rect_region(l[0], Rect2(x, y, sw, sw), src, l[1])
	if not emote.is_empty():
		var tex := Avatar.emote_texture(emote)
		if tex:
			var es := maxf(1.0, pixel_scale * 0.75) * Avatar.FRAME
			draw_texture_rect_region(tex, Rect2(x + sw * 0.55, y - es * 0.6, es, es),
				Rect2(_emote_frame * Avatar.FRAME, 0, Avatar.FRAME, Avatar.FRAME))
	if show_name and not nickname.is_empty():
		UI.draw_tag(self, Vector2(0, head_room() + sw + name_size * 1.25), nickname,
			HORIZONTAL_ALIGNMENT_CENTER, size.x, name_size, UI.C_MAP_TEXT,
			Color(0, 0, 0, 0), size.x - 8)
