class_name AvatarPicker
extends Window
## Avatar editor (adapted from ustc-discussion-student). Opens on the persona's saved look,
## or on the default generated from its id. Saved locally per persona (App.set_look).

const HAIR_BASE := {"dark": Color8(143, 119, 114), "light": Color8(230, 216, 108)}
const ROWS := [["body", "体型"], ["skin", "肤色"], ["hair", "发型"], ["hair_color", "发色"], ["eyes", "眼睛"],
	["outfit", "服装"], ["outfit_color", "服装颜色"], ["glasses", "眼镜"]]

## The App autoload, injected on creation (a global name would not resolve in test scripts).
var app: Node
var _persona := ""
var _spec := {}
var _preview: AvatarView
var _turns := []    # small previews, one per facing
var _values := {}   # key -> [Label, ColorRect]
var _subtitle: Label

## Layout size in units; App.show_window scales it to the screen.
const BASE_SIZE := Vector2i(760, 660)

func _init() -> void:
	title = "Anima · 换形象"
	size = BASE_SIZE
	close_requested.connect(hide)

func _ready() -> void:
	var bg := Panel.new()
	bg.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var sb := StyleBoxFlat.new()
	sb.bg_color = UI.C_BG
	bg.add_theme_stylebox_override("panel", sb)
	add_child(bg)
	var center := CenterContainer.new()
	center.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(center)
	var panel := PanelContainer.new()
	center.add_child(panel)
	var col := VBoxContainer.new()
	col.add_theme_constant_override("separation", 14)
	panel.add_child(col)
	col.add_child(UI.heading("换形象", 22))
	_subtitle = UI.label("", 13, UI.C_MUTED)
	col.add_child(_subtitle)

	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 28)
	col.add_child(row)
	var left := VBoxContainer.new()
	left.add_theme_constant_override("separation", 8)
	row.add_child(left)
	var stage := PanelContainer.new()
	var st := StyleBoxFlat.new()
	st.bg_color = UI.C_BG
	st.set_corner_radius_all(6)
	st.set_content_margin_all(12)
	stage.add_theme_stylebox_override("panel", st)
	left.add_child(stage)
	_preview = AvatarView.new()
	_preview.pixel_scale = 7
	_preview.show_name = false
	_preview.walking = true
	stage.add_child(_preview)
	var turns := HBoxContainer.new()
	turns.alignment = BoxContainer.ALIGNMENT_CENTER
	left.add_child(turns)
	for f in [Avatar.Facing.DOWN, Avatar.Facing.LEFT, Avatar.Facing.UP, Avatar.Facing.RIGHT]:
		var a := AvatarView.new()
		a.pixel_scale = 2
		a.show_name = false
		a.facing = f
		turns.add_child(a)
		_turns.append(a)

	var opts := GridContainer.new()
	opts.columns = 5
	opts.add_theme_constant_override("h_separation", 8)
	opts.add_theme_constant_override("v_separation", 10)
	row.add_child(opts)
	for r in ROWS:
		var key: String = r[0]
		opts.add_child(UI.label(r[1], 15, UI.C_MUTED))
		var prev := Button.new()
		prev.text = "◀"
		prev.pressed.connect(_cycle.bind(key, -1))
		opts.add_child(prev)
		var swatch := ColorRect.new()
		swatch.custom_minimum_size = Vector2(22, 22)
		swatch.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		opts.add_child(swatch)
		var l := UI.label("", 15)
		l.custom_minimum_size = Vector2(130, 30)
		l.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
		opts.add_child(l)
		var next := Button.new()
		next.text = "▶"
		next.pressed.connect(_cycle.bind(key, 1))
		opts.add_child(next)
		_values[key] = [l, swatch]

	var actions := HBoxContainer.new()
	actions.alignment = BoxContainer.ALIGNMENT_END
	actions.add_theme_constant_override("separation", 10)
	col.add_child(actions)
	var reset := Button.new()
	reset.text = "恢复默认"
	reset.pressed.connect(func() -> void:
		_spec = Avatar.default_spec(_persona)
		_refresh())
	actions.add_child(reset)
	var rnd := Button.new()
	rnd.text = "随机"
	rnd.pressed.connect(func() -> void:
		var rng := RandomNumberGenerator.new()
		rng.randomize()
		_spec = Avatar.random_spec(rng)
		_refresh())
	actions.add_child(rnd)
	var cancel := Button.new()
	cancel.text = "取消"
	cancel.pressed.connect(hide)
	actions.add_child(cancel)
	var save := Button.new()
	save.text = "保存形象"
	save.pressed.connect(_submit)
	actions.add_child(save)
	if not _persona.is_empty() or _spec.is_empty():
		edit(_persona)

## Load the look of a persona into the editor.
func edit(persona_id: String) -> void:
	_persona = persona_id
	_spec = Avatar.normalize(app.look(persona_id), persona_id)
	if _subtitle == null:
		return
	var who: String = app.persona_name if persona_id == app.persona_id and not app.persona_name.is_empty() else "桌宠"
	_subtitle.text = "给%s挑一个样子。形象只保存在这台电脑上。" % who
	_refresh()

func _options(key: String) -> Array:
	match key:
		"body": return Avatar.BODIES
		"skin": return range(Avatar.SKIN_LABELS.size())
		"hair": return Avatar.ids("hair")
		"hair_color": return range(Avatar.HAIR_COLORS.size())
		"eyes": return Avatar.ids("eyes")
		"outfit": return Avatar.ids("outfits", _spec.body)
		"outfit_color": return range(Avatar.OUTFIT_COLORS.size())
		"glasses": return [""] + Avatar.ids("glasses")
	return []

func _cycle(key: String, dir: int) -> void:
	var opts := _options(key)
	var i := posmod(opts.find(_spec[key]) + dir, opts.size())
	if key == "body":  # keep the same outfit slot on the other body
		var slot: int = Avatar.ids("outfits", _spec.body).find(_spec.outfit)
		_spec.body = opts[i]
		_spec.outfit = Avatar.ids("outfits", _spec.body)[maxi(slot, 0)]
	else:
		_spec[key] = opts[i]
	_refresh()

func _part_label(id: String, word: String) -> String:
	return "%s式%s %d" % ["女" if id.begins_with("f") else "男", word, int(id.substr(1))]

func _refresh() -> void:
	_preview.spec = _spec.duplicate()
	for a in _turns:
		a.spec = _spec.duplicate()
	for key in _values:
		var l: Label = _values[key][0]
		var sw: ColorRect = _values[key][1]
		sw.color = Color(0, 0, 0, 0)  # keep the grid cell; only colour rows show a swatch
		match key:
			"body": l.text = Avatar.BODY_LABELS[_spec.body]
			"skin":
				l.text = Avatar.SKIN_LABELS[_spec.skin]
				sw.color = [Color("#f7dcc4"), Color("#f0c39c"), Color("#d39a6a"), Color("#9a6440")][_spec.skin]
			"hair": l.text = _part_label(_spec.hair, "发型")
			"hair_color":
				var hc: Array = Avatar.HAIR_COLORS[_spec.hair_color]
				l.text = hc[0]
				sw.color = HAIR_BASE[hc[1]] * hc[2]
			"eyes": l.text = _part_label(_spec.eyes, "眼睛")
			"outfit": l.text = Avatar.outfit_label(_spec.body, _spec.outfit)
			"outfit_color":
				l.text = Avatar.OUTFIT_COLORS[_spec.outfit_color][0]
				sw.color = Color(0.82, 0.82, 0.82) * Avatar.OUTFIT_COLORS[_spec.outfit_color][1]
			"glasses": l.text = "无" if str(_spec.glasses).is_empty() else "眼镜 %s" % str(_spec.glasses).substr(1)

func _submit() -> void:
	app.set_look(_persona, _spec.duplicate())
	hide()
