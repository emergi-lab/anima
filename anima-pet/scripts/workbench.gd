class_name Workbench
extends Window
## 蒸馏工作台: add source texts, pick the character (and its look), run the distillation,
## then review every entry against its evidence (accept, reject, rewrite, add) and commit
## it as the initial B0. Parchment theme shared with the rest of the app (UI, App theme).

const POLL_SECONDS := 2.0
const LAYERS := ["L3", "L2", "L1"]
const LAYER_NAMES := {"L3": "叙事身份", "L2": "特征适应", "L1": "特质"}
const CONSTRUCTS := {
	"overall_identity": "总体身份", "imago": "角色形象", "life_course": "人生经历",
	"need": "需要", "value": "价值", "goal": "目标", "strategy": "应对策略", "trait": "特质",
}
const CONSTRUCT_LAYER := {
	"overall_identity": "L3", "imago": "L3", "life_course": "L3",
	"need": "L2", "value": "L2", "goal": "L2", "strategy": "L2", "trait": "L1",
}
const STATUS_NAMES := {"proposed": "待审", "accepted": "通过", "rejected": "驳回", "edited": "已改写", "added": "新增"}
## Text colours on parchment (all ≥ 4.5:1 on C_INPUT).
const STATUS_COLORS := {
	"proposed": Color("#5f4630"), "accepted": Color("#2f6b2a"), "rejected": Color("#9b1c14"),
	"edited": Color("#1f5578"), "added": Color("#7a4f00"),
}
const STAGE_NAMES := {"draft": "准备素材", "running": "蒸馏中", "review": "审阅", "failed": "中断", "committed": "已导入"}
const STEP_NAMES := {"extracting": "正在从素材里找证据", "inducing": "正在归纳成信念", "reviewing": "正在复核"}
const STEPS := ["① 素材", "② 选角色", "③ 蒸馏", "④ 审阅", "⑤ 导入"]
const ACCEPTED := ["accepted", "edited", "added"]
const LEVELS := ["很低", "较低", "中等", "较高", "很高"]
const SURE := ["把握很小", "不太有把握", "一般", "比较有把握", "很有把握"]

## Horizontal bar for a 0–1 value with a word next to it (from the student "我的人格" page).
class Bar extends Control:
	var value := 0.0

	func _init() -> void:
		custom_minimum_size = Vector2(160, 12)
		size_flags_vertical = Control.SIZE_SHRINK_CENTER

	func set_value(v: float) -> void:
		value = clampf(v, 0.0, 1.0)
		queue_redraw()

	func _draw() -> void:
		var r := Rect2(Vector2.ZERO, size)
		draw_rect(r, UI.C_INPUT)
		draw_rect(Rect2(r.position, Vector2(r.size.x * value, r.size.y)), UI.C_PRIMARY)
		draw_rect(r, UI.C_FRAME, false, 1.0)

## The App autoload, injected on creation (a global name would not resolve in test scripts).
var app: Node
var _list: Array = []  # Distillation summaries, newest first
var _current: Dictionary = {}
var _entries: Array = []  # ReviewedEntry
var _entry_index := -1
var _poll := 0.0
var _busy := false

var _sidebar: VBoxContainer  # "我的蒸馏": one row per distillation
var _drop_dialog: ConfirmationDialog
var _drop_id := ""
var _status: Label
var _steps: Array[Label] = []
var _portrait: AvatarView
var _setup: Control
var _sources: ItemList
var _characters: HFlowContainer
var _name: LineEdit
var _aliases: LineEdit
var _persona_id: LineEdit
var _setup_avatar: AvatarView
var _preview: Label
var _progress: Control
var _runner: AvatarView
var _step: Label
var _bar: ProgressBar
var _usage: Label
var _error: Label
var _resume: Button
var _fix: Button  # a failed run the model settings can fix: go there
var _no_model: PanelContainer  # banner while no API key is set
var _review: Control
var _done_banner: PanelContainer
var _done_text: Label
var _done: ScrollContainer  # the page shown once the persona is imported
var _done_portrait: AvatarView
var _done_title: Label
var _done_counts: Label
var _done_bars: GridContainer
var _done_identity: VBoxContainer
var _back_link: LinkButton
var _back_note: Label
var _reviewing := false  # a committed distillation opened for more review
var _back_done: Button
var _deleted := false  # its persona was deleted since; importing again brings it back
var _done_loaded := ""  # version id the done page shows
var _counts: Label
var _tree: Tree
var _entry_title: Label
var _entry_status: Label
var _entry_meta: Label
var _strength_row: HBoxContainer
var _strength: Bar
var _strength_word: Label
var _sure: Bar
var _sure_word: Label
var _text: TextEdit
var _note_box: PanelContainer
var _note: Label
var _evidence: ItemList
var _context: RichTextLabel
var _files: FileDialog
var _add_dialog: ConfirmationDialog
var _add_construct: OptionButton
var _add_id: LineEdit
var _add_text: TextEdit

## Layout size in units; App.show_window scales it to the screen.
const BASE_SIZE := Vector2i(1460, 820)
const MIN_SIZE := Vector2i(1180, 660)
const SIDEBAR_W := 230

func _init() -> void:
	title = "Anima · 蒸馏工作台"
	size = BASE_SIZE
	min_size = MIN_SIZE
	# Fixed size: autowrapped labels would otherwise report huge minimum heights and grow it.
	wrap_controls = false
	close_requested.connect(hide)

func _ready() -> void:
	var bg := Panel.new()
	bg.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	bg.add_theme_stylebox_override("panel", _flat(UI.C_BG))
	add_child(bg)
	var margin := MarginContainer.new()
	margin.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	for side in ["left", "right", "top", "bottom"]:
		margin.add_theme_constant_override("margin_" + side, 16)
	add_child(margin)
	var page := HBoxContainer.new()
	page.add_theme_constant_override("separation", 16)
	margin.add_child(page)
	page.add_child(_build_sidebar())
	var root := VBoxContainer.new()
	root.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	root.add_theme_constant_override("separation", 12)
	page.add_child(root)
	root.add_child(_build_header())
	root.add_child(_build_no_model())
	root.add_child(_build_steps())
	_setup = _build_setup()
	_progress = _build_progress()
	_review = _build_review()
	_done = _build_done()
	for panel in [_setup, _progress, _review, _done]:
		panel.size_flags_vertical = Control.SIZE_EXPAND_FILL
		root.add_child(panel)
	_build_dialogs()
	app.look_changed.connect(_refresh_avatars)
	app.model_ready_changed.connect(func() -> void: _no_model.visible = not app.model_ready)
	visibility_changed.connect(func() -> void:
		if visible:
			app.check_model())
	_render()
	_refresh_list()

func _process(delta: float) -> void:
	if not visible or _current.get("stage", "") != "running":
		return
	_poll += delta
	if _poll >= POLL_SECONDS and not _busy:
		_poll = 0.0
		_load(str(_current.distillation_id))

# ---- layout -------------------------------------------------------------------------------

func _build_header() -> HBoxContainer:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 10)
	var brand := UI.pixel_label("Anima · 蒸馏工作台", 24)
	row.add_child(brand)
	_status = UI.label("", 14, UI.C_MUTED)
	_status.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	row.add_child(_status)
	_portrait = AvatarView.new()
	_portrait.pixel_scale = 2
	_portrait.show_name = false
	row.add_child(_portrait)
	return row

## Shown while no API key is set: materials and the target can be prepared, distilling cannot.
func _build_no_model() -> PanelContainer:
	_no_model = PanelContainer.new()
	var sb := StyleBoxFlat.new()
	sb.bg_color = Color("#fbeec2")
	sb.border_color = Color("#c9a227")
	sb.set_border_width_all(2)
	sb.set_corner_radius_all(4)
	sb.set_content_margin_all(8)
	sb.content_margin_left = 12
	_no_model.add_theme_stylebox_override("panel", sb)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 12)
	_no_model.add_child(row)
	var text := UI.label("还没有配置模型 API：可以先添加材料、选好角色，开始蒸馏前需要在「设置 → 模型服务」里填上你自己的 API。", 14)
	text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(text)
	row.add_child(_button("去设置", app.open_model_settings))
	_no_model.visible = not app.model_ready
	return _no_model

## "我的蒸馏": every distillation by its character, its stage and date; click to open one,
## ✕ drops a draft that has not produced entries yet.
func _build_sidebar() -> VBoxContainer:
	var col := VBoxContainer.new()
	col.custom_minimum_size.x = SIDEBAR_W
	col.add_theme_constant_override("separation", 10)
	col.add_child(UI.heading("我的蒸馏", 20))
	var scroll := ScrollContainer.new()
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	col.add_child(scroll)
	_sidebar = VBoxContainer.new()
	_sidebar.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_sidebar.add_theme_constant_override("separation", 8)
	scroll.add_child(_sidebar)
	col.add_child(_button("＋ 新建蒸馏", _create))
	return col

## Display name of a distillation: its character once chosen.
func _label_of(d: Dictionary) -> String:
	var target = d.get("target")
	return str(target.name) if target is Dictionary and not str(target.get("name", "")).is_empty() \
		else "新的蒸馏"

func _fill_sidebar() -> void:
	for c in _sidebar.get_children():
		_sidebar.remove_child(c)
		c.queue_free()
	var alive := {}
	for p in app.personas:
		alive[str(p.persona_id)] = true
	for d in _list:
		_sidebar.add_child(_sidebar_row(d, alive))

func _sidebar_row(d: Dictionary, alive: Dictionary) -> Control:
	var id := str(d.distillation_id)
	var current := id == str(_current.get("distillation_id", ""))
	var stage := str(d.stage)
	var pid := str(d.persona_id) if d.get("persona_id") != null else ""
	var panel := PanelContainer.new()
	var box := StyleBoxFlat.new()
	box.bg_color = UI.C_SURFACE if current else UI.C_INPUT
	box.set_border_width_all(3 if current else 2)
	box.border_color = UI.C_PRIMARY if current else Color(UI.C_FRAME, 0.5)
	box.set_corner_radius_all(4)
	box.set_content_margin_all(8)
	panel.add_theme_stylebox_override("panel", box)
	panel.mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	panel.gui_input.connect(func(e: InputEvent) -> void:
		if e is InputEventMouseButton and e.pressed and e.button_index == MOUSE_BUTTON_LEFT:
			_load(id))
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	row.mouse_filter = Control.MOUSE_FILTER_IGNORE
	panel.add_child(row)
	var icon := TextureRect.new()
	icon.custom_minimum_size = Vector2(44, 44)
	icon.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	icon.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
	icon.mouse_filter = Control.MOUSE_FILTER_IGNORE
	if not pid.is_empty():
		icon.texture = app.persona_icon(pid, 2)
	row.add_child(icon)
	var text := VBoxContainer.new()
	text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	text.mouse_filter = Control.MOUSE_FILTER_IGNORE
	row.add_child(text)
	var name_label := UI.heading(_label_of(d), 16)
	name_label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	text.add_child(name_label)
	var word: String = STAGE_NAMES.get(stage, stage)
	if stage == "committed" and not alive.has(pid):
		word = "人格已删除"
	var info := UI.label("%s · %s" % [word, UI.local_time(d.created_at).substr(5, 5)], 13,
		Color("#2f6b2a") if stage == "committed" and alive.has(pid) else UI.C_MUTED)
	info.mouse_filter = Control.MOUSE_FILTER_IGNORE
	text.add_child(info)
	if stage in ["draft", "failed"]:
		var drop := Button.new()
		drop.text = "✕"
		drop.flat = true
		drop.tooltip_text = "删除这个草稿"
		drop.size_flags_vertical = Control.SIZE_SHRINK_CENTER
		drop.pressed.connect(func() -> void: _ask_drop(d))
		row.add_child(drop)
	return panel

func _ask_drop(d: Dictionary) -> void:
	_drop_id = str(d.distillation_id)
	_drop_dialog.dialog_text = "删除「%s」这个草稿？已上传的素材也会一起删除。" % _label_of(d)
	app.popup_dialog(_drop_dialog, Vector2i(460, 170), self)

func _drop() -> void:
	var r := await _call("/v1/distillations/%s:discard" % _drop_id, HTTPClient.METHOD_POST, {})
	if not r.ok:
		return
	var keep := "" if _drop_id == str(_current.get("distillation_id", "")) \
		else str(_current.get("distillation_id", ""))
	if keep.is_empty():
		_current = {}
	_refresh_list(keep)

func _build_steps() -> HBoxContainer:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 6)
	for i in STEPS.size():
		if i > 0:
			row.add_child(UI.label("──", 14, UI.C_MUTED))
		var chip := UI.label(STEPS[i], 15)
		chip.add_theme_stylebox_override("normal", _chip_box(UI.C_SURFACE))
		row.add_child(chip)
		_steps.append(chip)
	return row

func _build_setup() -> HBoxContainer:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 14)
	var left := _card(row, "① 素材")
	left.get_parent().size_flags_stretch_ratio = 0.8
	left.add_child(UI.label("台词、聊天记录、小说、日记都可以；需要 UTF-8 编码的 .txt / .md。", 13, UI.C_MUTED))
	var add := _button("添加文本文件…", func() -> void: _files.popup_centered_ratio(0.7))
	add.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	left.add_child(add)
	_sources = ItemList.new()
	_sources.size_flags_vertical = Control.SIZE_EXPAND_FILL
	left.add_child(_sources)

	var right := _card(row, "② 选角色")
	right.add_child(UI.label("素材里常出现的说话人（点一下填入名字）：", 13, UI.C_MUTED))
	_characters = HFlowContainer.new()
	_characters.add_theme_constant_override("h_separation", 6)
	_characters.add_theme_constant_override("v_separation", 6)
	right.add_child(_characters)
	var form := HBoxContainer.new()
	form.add_theme_constant_override("separation", 16)
	right.add_child(form)
	var grid := GridContainer.new()
	grid.columns = 2
	grid.add_theme_constant_override("v_separation", 8)
	grid.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_name = _field(grid, "名字", "素材中最常用的称呼，如：悟空")
	_name.text_changed.connect(func(_t: String) -> void: _refresh_avatars())
	_aliases = _field(grid, "别名", "用逗号或顿号分隔，如：美猴王、大圣、行者")
	_persona_id = _field(grid, "人格 ID", "英文、数字、- _ . :")
	_persona_id.text_changed.connect(func(_t: String) -> void: _refresh_avatars())
	form.add_child(grid)
	var look := VBoxContainer.new()
	look.alignment = BoxContainer.ALIGNMENT_CENTER
	_setup_avatar = AvatarView.new()
	_setup_avatar.pixel_scale = 3
	_setup_avatar.walking = true
	_setup_avatar.facing = Avatar.Facing.DOWN
	look.add_child(_setup_avatar)
	look.add_child(_button("捏形象…", func() -> void: app.open_picker(_persona_id.text.strip_edges())))
	form.add_child(look)
	var spacer := Control.new()
	spacer.size_flags_vertical = Control.SIZE_EXPAND_FILL
	right.add_child(spacer)
	_preview = UI.label("", 14, UI.C_PRIMARY)
	_preview.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	right.add_child(_preview)
	var actions := HBoxContainer.new()
	actions.alignment = BoxContainer.ALIGNMENT_END
	actions.add_theme_constant_override("separation", 10)
	actions.add_child(_button("预估用量", _preview_target))
	var go := _button("开始蒸馏", _start)
	go.custom_minimum_size.x = 140
	actions.add_child(go)
	right.add_child(actions)
	return row

func _build_progress() -> CenterContainer:
	var center := CenterContainer.new()
	var panel := PanelContainer.new()
	panel.custom_minimum_size = Vector2(620, 0)
	center.add_child(panel)
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 12)
	panel.add_child(box)
	_runner = AvatarView.new()
	_runner.pixel_scale = 4
	_runner.show_name = false
	_runner.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	box.add_child(_runner)
	_step = UI.heading("", 20)
	_step.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	box.add_child(_step)
	_bar = ProgressBar.new()
	_bar.custom_minimum_size.y = 22
	box.add_child(_bar)
	_usage = UI.label("", 14, UI.C_MUTED)
	_usage.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	box.add_child(_usage)
	_error = UI.label("", 14, UI.C_ERROR)
	_error.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(_error)
	_resume = _button("继续（只重试失败的片段）", _resume_run)
	_resume.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	box.add_child(_resume)
	_fix = _button("去设置", app.open_model_settings)
	_fix.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	box.add_child(_fix)
	return center

func _build_review() -> VBoxContainer:
	var col := VBoxContainer.new()
	col.add_theme_constant_override("separation", 10)
	_done_banner = PanelContainer.new()
	_done_banner.add_theme_stylebox_override("panel", _note_style(Color("#e3f0d3"), Color("#2f6b2a")))
	var banner := HBoxContainer.new()
	_done_text = UI.label("", 15, Color("#2f6b2a"))
	_done_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_done_text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	banner.add_child(_done_text)
	_back_done = _button("返回完成页", func() -> void:
		_reviewing = false
		_render())
	banner.add_child(_back_done)
	_done_banner.add_child(banner)
	col.add_child(_done_banner)

	var split := HSplitContainer.new()
	split.size_flags_vertical = Control.SIZE_EXPAND_FILL
	split.split_offset = 0
	col.add_child(split)
	var left := _card(split, "④ 审阅条目")
	left.custom_minimum_size.x = 400
	_counts = UI.label("", 13, UI.C_MUTED)
	_counts.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	left.add_child(_counts)
	_tree = Tree.new()
	_tree.hide_root = true
	_tree.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_tree.item_selected.connect(_on_tree_selected)
	left.add_child(_tree)
	var buttons := HBoxContainer.new()
	buttons.add_theme_constant_override("separation", 8)
	buttons.add_child(_button("新增条目…", func() -> void: app.popup_dialog(_add_dialog, Vector2i(520, 360), self)))
	buttons.add_child(_button("待审的全部通过", _accept_pending))
	left.add_child(buttons)
	var commit := _button("⑤ 确认导入，让它住进桌面", _commit)
	commit.custom_minimum_size.y = 40
	left.add_child(commit)

	var right := _card(split, "")
	var head := HBoxContainer.new()
	_entry_title = UI.heading("选择左侧条目", 19)
	_entry_title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	head.add_child(_entry_title)
	_entry_status = UI.label("", 14)
	_entry_status.add_theme_stylebox_override("normal", _chip_box(UI.C_INPUT))
	head.add_child(_entry_status)
	right.add_child(head)
	_entry_meta = UI.label("", 13, UI.C_MUTED)
	_entry_meta.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	right.add_child(_entry_meta)
	_strength_row = HBoxContainer.new()
	_strength_row.add_theme_constant_override("separation", 8)
	_strength_row.add_child(UI.label("强度", 13, UI.C_MUTED))
	_strength = Bar.new()
	_strength_row.add_child(_strength)
	_strength_word = UI.label("", 13)
	_strength_word.custom_minimum_size.x = 48
	_strength_row.add_child(_strength_word)
	_strength_row.add_child(UI.label("把握", 13, UI.C_MUTED))
	_sure = Bar.new()
	_strength_row.add_child(_sure)
	_sure_word = UI.label("", 13)
	_strength_row.add_child(_sure_word)
	right.add_child(_strength_row)
	_text = TextEdit.new()
	_text.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	_text.custom_minimum_size.y = 92
	right.add_child(_text)
	var act := HBoxContainer.new()
	act.add_theme_constant_override("separation", 8)
	act.add_child(_button("通过", func() -> void: _patch({"status": "accepted"})))
	act.add_child(_button("驳回", func() -> void: _patch({"status": "rejected"})))
	act.add_child(_button("保存改写", _save_text))
	act.add_child(_button("恢复待审", func() -> void: _patch({"status": "proposed"})))
	right.add_child(act)
	_note_box = PanelContainer.new()
	_note_box.add_theme_stylebox_override("panel", _note_style(UI.C_MINE_BG, Color("#b5794a")))
	_note = UI.label("", 13, UI.C_ME)
	_note.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_note_box.add_child(_note)
	right.add_child(_note_box)
	right.add_child(UI.heading("证据（点一条看原文）", 15))
	_evidence = ItemList.new()
	_evidence.custom_minimum_size.y = 110
	_evidence.item_selected.connect(_on_evidence_selected)
	right.add_child(_evidence)
	_context = RichTextLabel.new()
	_context.bbcode_enabled = true
	_context.selection_enabled = true
	_context.size_flags_vertical = Control.SIZE_EXPAND_FILL
	right.add_child(_context)
	return col

func _build_dialogs() -> void:
	_files = FileDialog.new()
	_files.use_native_dialog = true
	_files.access = FileDialog.ACCESS_FILESYSTEM
	_files.file_mode = FileDialog.FILE_MODE_OPEN_FILES
	_files.filters = PackedStringArray(["*.txt, *.md ; 文本（UTF-8）"])
	_files.files_selected.connect(_add_files)
	add_child(_files)

	_drop_dialog = ConfirmationDialog.new()
	_drop_dialog.title = "删除草稿"
	_drop_dialog.ok_button_text = "删除"
	_drop_dialog.cancel_button_text = "取消"
	_drop_dialog.confirmed.connect(_drop)
	add_child(_drop_dialog)

	_add_dialog = ConfirmationDialog.new()
	_add_dialog.title = "新增条目"
	_add_dialog.ok_button_text = "新增"
	_add_dialog.cancel_button_text = "取消"
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 8)
	box.custom_minimum_size = Vector2(480, 260)
	_add_construct = OptionButton.new()
	for key in CONSTRUCTS:
		_add_construct.add_item("%s · %s" % [LAYER_NAMES[CONSTRUCT_LAYER[key]], CONSTRUCTS[key]])
		_add_construct.set_item_metadata(_add_construct.item_count - 1, key)
	box.add_child(_add_construct)
	_add_id = LineEdit.new()
	_add_id.placeholder_text = "构念名（英文小写，如 loyalty），可留空"
	box.add_child(_add_id)
	_add_text = TextEdit.new()
	_add_text.placeholder_text = "用第一人称写：我……"
	_add_text.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	_add_text.size_flags_vertical = Control.SIZE_EXPAND_FILL
	box.add_child(_add_text)
	_add_dialog.add_child(box)
	_add_dialog.confirmed.connect(_add_entry)
	add_child(_add_dialog)

## The page after import: who now lives on the desktop, a glance at the persona, what to do next.
func _build_done() -> ScrollContainer:
	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	var col := VBoxContainer.new()
	col.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	col.add_theme_constant_override("separation", 14)
	scroll.add_child(col)

	var hero := HBoxContainer.new()
	hero.add_theme_constant_override("separation", 24)
	col.add_child(hero)
	_done_portrait = AvatarView.new()
	_done_portrait.pixel_scale = 5
	_done_portrait.show_name = false
	_done_portrait.emote = "sparkle"
	hero.add_child(_done_portrait)
	var words := VBoxContainer.new()
	words.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	words.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	words.add_theme_constant_override("separation", 8)
	hero.add_child(words)
	_done_title = UI.pixel_label("", 30, Color("#2f6b2a"))
	words.add_child(_done_title)
	_done_counts = UI.label("", 16)
	_done_counts.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	words.add_child(_done_counts)

	var glance := HBoxContainer.new()
	glance.add_theme_constant_override("separation", 14)
	col.add_child(glance)
	var traits := _card(glance, "大五人格")
	_done_bars = GridContainer.new()
	_done_bars.columns = 3
	_done_bars.add_theme_constant_override("h_separation", 12)
	_done_bars.add_theme_constant_override("v_separation", 8)
	traits.add_child(_done_bars)
	var identity := _card(glance, "它怎么看自己")
	_done_identity = VBoxContainer.new()
	_done_identity.add_theme_constant_override("separation", 6)
	identity.add_child(_done_identity)

	var next := HBoxContainer.new()
	next.add_theme_constant_override("separation", 12)
	col.add_child(next)
	var talk := _button("和它说话", func() -> void:
		app.talk_requested.emit()
		hide())
	talk.add_theme_font_size_override("font_size", 18)
	next.add_child(talk)
	next.add_child(_button("看它的完整人格", app.open_persona))
	next.add_child(_button("换形象", func() -> void: app.open_picker(_persona())))
	next.add_child(_button("再蒸馏一个", _create))

	var back := HBoxContainer.new()
	back.add_theme_constant_override("separation", 10)
	col.add_child(back)
	_back_link = LinkButton.new()
	_back_link.text = "回到审阅、继续修改"
	_back_link.add_theme_color_override("font_color", UI.C_PRIMARY)
	_back_link.pressed.connect(func() -> void:
		_reviewing = true
		_render())
	back.add_child(_back_link)
	_back_note = UI.label("", 14, UI.C_MUTED)
	_back_note.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_back_note.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	back.add_child(_back_note)
	return scroll

func _load_done() -> void:
	var pid := str(_current.get("persona_id", ""))
	if pid.is_empty():
		return
	_done_title.text = "%s 已经住进你的桌面了" % _display_name()
	var h: Dictionary = await app.api.request_json("/v1/personas/%s/history" % pid.uri_encode())
	if not h.ok and int(h.get("status", 0)) == 404:  # deleted: back to review, ready to import
		_deleted = true
		_reviewing = true
		_render()
		return
	if not h.ok or h.data.is_empty() or _done_loaded == str(h.data[0].version_id):
		return
	var latest: Dictionary = h.data[0]
	var v := await _call("/v1/personas/%s/versions/%s" % [pid.uri_encode(), latest.version_id])
	if not v.ok:
		return
	_done_loaded = str(latest.version_id)
	var known: Array = v.data.entries.filter(func(e: Dictionary) -> bool: return e.epistemic_status != "unknown")
	var unknown: Array = v.data.entries.filter(func(e: Dictionary) -> bool: return e.epistemic_status == "unknown")
	var count := func(layer: String) -> int:
		return known.filter(func(e: Dictionary) -> bool: return e.layer == layer).size()
	_done_counts.text = "导入 %d 条信念：叙事身份 %d 条 · 特征适应 %d 条 · 特质 %d 条%s" % [
		known.size(), count.call("L3"), count.call("L2"), count.call("L1"),
		"\n%d 类素材里没有体现，保持未知（%s）。" % [unknown.size(), "、".join(unknown.map(
			func(e: Dictionary) -> String: return str(CONSTRUCTS.get(e.construct, e.construct))))]
		if not unknown.is_empty() else ""]
	for c in _done_bars.get_children():
		c.queue_free()
	for d in PersonaWindow.BIG_FIVE:
		var facets: Array = known.filter(func(e: Dictionary) -> bool:
			return e.layer == "L1" and str(e.construct_id).get_slice(".", 1) == d \
				and typeof(e.get("value")) == TYPE_DICTIONARY and e.value.has("baseline"))
		_done_bars.add_child(UI.label(PersonaWindow.BIG_FIVE[d][0], 15))
		var bar := Bar.new()
		bar.custom_minimum_size.x = 200
		var word := "未知"
		if not facets.is_empty():
			var mean: float = facets.reduce(func(a: float, e: Dictionary) -> float: return a + float(e.value.baseline), 0.0) / facets.size()
			bar.set_value(mean)
			word = PersonaWindow.TRAIT_LEVELS[clampi(int(mean * 5.0), 0, 4)]
		_done_bars.add_child(bar)
		_done_bars.add_child(UI.label(word, 14, UI.C_MUTED if facets.is_empty() else UI.C_TEXT))
	for c in _done_identity.get_children():
		c.queue_free()
	var l3: Array = known.filter(func(e: Dictionary) -> bool: return e.layer == "L3")
	l3.sort_custom(func(a: Dictionary, b: Dictionary) -> bool:  # self-view first, then strongest
		var sa: bool = a.construct == "overall_identity"
		var sb: bool = b.construct == "overall_identity"
		return sa if sa != sb else _weight(a) > _weight(b))
	for e in l3.slice(0, 3):
		var line := str(e.text).get_slice("。", 0) + "。"
		var l := UI.label("· " + line, 15)
		l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		l.custom_minimum_size.x = 380
		_done_identity.add_child(l)
	if l3.is_empty():
		_done_identity.add_child(UI.label("素材里没有它谈自己的内容。", 15, UI.C_MUTED))
	var evolved := int(latest.k) > 0
	_back_link.disabled = evolved
	_back_note.text = ("它已经演化到 B%d，改动请用人格窗口里的「演化」。" % int(latest.k)) if evolved \
		else "改完再点一次导入，会覆盖它现在的人格（B0）。"

static func _weight(e: Dictionary) -> float:
	var v: Variant = e.get("value")
	return float(v.baseline) if typeof(v) == TYPE_DICTIONARY and (v as Dictionary).has("baseline") else 0.0

## A parchment card with an optional heading; returns its content column.
func _card(parent: Control, heading: String) -> VBoxContainer:
	var panel := PanelContainer.new()
	panel.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	panel.size_flags_vertical = Control.SIZE_EXPAND_FILL
	parent.add_child(panel)
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 8)
	panel.add_child(box)
	if not heading.is_empty():
		box.add_child(UI.heading(heading, 18))
	return box

func _button(text: String, pressed: Callable) -> Button:
	var b := Button.new()
	b.text = text
	b.pressed.connect(pressed)
	return b

func _field(grid: GridContainer, label: String, hint: String) -> LineEdit:
	grid.add_child(UI.label(label, 15, UI.C_MUTED))
	var e := LineEdit.new()
	e.placeholder_text = hint
	e.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	grid.add_child(e)
	return e

func _flat(color: Color) -> StyleBoxFlat:
	var b := StyleBoxFlat.new()
	b.bg_color = color
	return b

func _chip_box(color: Color) -> StyleBoxFlat:
	var b := StyleBoxFlat.new()
	b.bg_color = color
	b.set_border_width_all(2)
	b.border_color = Color(UI.C_FRAME, 0.5)
	b.set_corner_radius_all(3)
	b.content_margin_left = 10
	b.content_margin_right = 10
	b.content_margin_top = 3
	b.content_margin_bottom = 3
	return b

func _note_style(bg: Color, edge: Color) -> StyleBoxFlat:
	var b := StyleBoxFlat.new()
	b.bg_color = bg
	b.border_width_left = 4
	b.border_color = edge
	b.set_content_margin_all(8)
	b.content_margin_left = 12
	return b

# ---- data ---------------------------------------------------------------------------------

func _say(text: String, error := false) -> void:
	_status.text = text
	_status.add_theme_color_override("font_color", UI.C_ERROR if error else UI.C_MUTED)

func _call(path: String, method := HTTPClient.METHOD_GET, body: Variant = null) -> Dictionary:
	_busy = true
	var r: Dictionary = await app.api.request_json(path, method, body)
	_busy = false
	if not r.ok and app.model_trouble(r):
		_say(app.model_line(r), true)
		app.remind_model(self, r)
	elif not r.ok and r.get("code") == "persona_limit":
		_say("最多只能有 %d 个人格。先在右键「切换人格」→「管理人格…」里删掉一个，再导入新的。" % app.max_personas, true)
	elif not r.ok:
		_say(str(r.error), true)
	return r

func _base() -> String:
	return "/v1/distillations/" + str(_current.distillation_id)

func _refresh_list(select_id := "") -> void:
	var r := await _call("/v1/distillations")
	if not r.ok:
		return
	_list = r.data
	await app.refresh_personas()  # to mark distillations whose persona was deleted
	if _list.is_empty():
		_current = {}
		_render()
		_say("还没有蒸馏项目，点“新建蒸馏”开始。")
		return
	var index := 0
	for i in _list.size():
		if str(_list[i].distillation_id) == select_id:
			index = i
	_load(str(_list[index].distillation_id))

func _create() -> void:
	for d in _list:  # an empty draft is already there: use it rather than pile up another
		if d.stage == "draft" and d.sources.is_empty():
			_load(str(d.distillation_id))
			return
	var title_text := "蒸馏 " + Time.get_datetime_string_from_system(false, true).left(16)
	var r := await _call("/v1/distillations", HTTPClient.METHOD_POST, {"title": title_text})
	if r.ok:
		_refresh_list(str(r.data.distillation_id))

func _load(id: String) -> void:
	var r := await _call("/v1/distillations/" + id)
	if not r.ok:
		return
	var before := str(_current.get("stage", ""))
	var same := str(_current.get("distillation_id", "")) == id
	if not same:  # another distillation: its own form, not the last one's
		_reviewing = false
		_deleted = false
		_name.text = ""
		_aliases.text = ""
		_persona_id.text = ""
		_preview.text = ""
	_current = r.data
	var stage := str(_current.stage)
	if stage == "draft" and not same:
		_load_characters()
	if stage in ["review", "committed"] and (not same or before != stage):
		_load_entries()
	if not same or before != stage:
		_refresh_picker_label()
	_render()

func _refresh_picker_label() -> void:
	for i in _list.size():
		if str(_list[i].distillation_id) == str(_current.distillation_id):
			_list[i] = _current
	_fill_sidebar()

## Persona id and name of the current distillation (form fields before it has started).
func _persona() -> String:
	if _current.get("persona_id") != null and not _current.is_empty():
		return str(_current.persona_id)
	return _persona_id.text.strip_edges()

func _display_name() -> String:
	var target = _current.get("target")
	return str(target.name) if target is Dictionary else _name.text.strip_edges()

func _refresh_avatars() -> void:
	var id := _persona()
	for view in [_portrait, _setup_avatar, _runner, _done_portrait]:
		view.seed_text = id
		view.spec = app.look(id)

func _render() -> void:
	var stage := str(_current.get("stage", ""))
	_setup.visible = stage == "draft"
	_progress.visible = stage in ["running", "failed"]
	_review.visible = stage == "review" or (stage == "committed" and _reviewing)
	_done.visible = stage == "committed" and not _reviewing
	if _done.visible:
		_load_done()
	_portrait.visible = not _current.is_empty()
	# committed: every step is done (green), none is "current"
	var at: int = {"draft": 0, "running": 2, "failed": 2, "review": 3, "committed": STEPS.size()}.get(stage, -1)
	if stage == "draft" and not _current.sources.is_empty():
		at = 1
	for i in _steps.size():
		var chip := _steps[i]
		var color := UI.C_SURFACE_2 if i == at else (Color("#e3f0d3") if i < at else UI.C_SURFACE)
		chip.add_theme_stylebox_override("normal", _chip_box(color))
		chip.add_theme_color_override("font_color", UI.C_TEXT if i <= at else UI.C_MUTED)
	if _current.is_empty():
		return
	_say("%s · %s%s" % [_label_of(_current), STAGE_NAMES.get(stage, stage),
		"" if _current.persona_id == null else " · %s（%s）" % [_display_name(), _current.persona_id]])
	_sources.clear()
	for s in _current.sources:
		_sources.add_item("%s　%d 字，%d 段" % [s.name, s.chars, s.segments])
	if _persona_id.text.is_empty():
		_persona_id.text = "persona-" + str(_current.distillation_id).left(8)
	_refresh_avatars()
	var p: Dictionary = _current.progress
	var u: Dictionary = _current.usage
	_step.text = STEP_NAMES.get(_current.step, "蒸馏中") if stage == "running" else "蒸馏中断了"
	_runner.emote = "think" if stage == "running" else "sweat"
	_runner.walking = stage == "running"
	_runner.facing = Avatar.Facing.DOWN
	_bar.max_value = maxi(1, int(p.relevant))
	_bar.value = int(p.extracted)
	_usage.text = "片段 %d / %d（失败 %d）· 证据 %d 条 · 条目 %d 条\n模型调用 %d 次 · 输入 %d token · 输出 %d token" % [
		p.extracted, p.relevant, p.failed, p.evidence, p.entries,
		u.model_calls, u.prompt_tokens, u.completion_tokens]
	_error.text = "" if _current.error == null else str(_current.error)
	_resume.visible = stage == "failed"
	_fix.visible = stage == "failed" and app.model_trouble(_current.error)
	_done_banner.visible = stage == "committed"
	_back_done.visible = not _deleted
	_done_text.text = ("%s 已经被删除了。点「确认导入」可以让它重新住进桌面，之前的聊天和记忆不会回来。" if _deleted
		else "%s 已经住进桌面了。改完再点一次导入就会更新它。") % _display_name()
	_portrait.emote = {"running": "think", "review": "question", "committed": "sparkle"}.get(stage, "")
	if stage in ["review", "committed"] and _current.error != null:
		_say("%s · %s" % [_status.text, _current.error], true)

func _load_characters() -> void:
	var r := await _call(_base() + "/characters")
	for child in _characters.get_children():
		child.queue_free()
	if not r.ok:
		return
	if r.data.is_empty():
		_characters.add_child(UI.label("没认出说话人（小说叙述体很常见），直接在下面填名字即可。", 13, UI.C_MUTED))
	for c in r.data:
		var chip := _button("%s · %d" % [c.name, c.lines], func() -> void:
			_name.text = str(c.name)
			_refresh_avatars())
		_characters.add_child(chip)

func _add_files(paths: PackedStringArray) -> void:
	for path in paths:
		var text := FileAccess.get_file_as_string(path)
		if text.is_empty():
			_say("读不到 %s：需要 UTF-8 编码的文本文件。" % path.get_file(), true)
			continue
		var r := await _call(_base() + "/sources", HTTPClient.METHOD_POST,
			{"name": path.get_file(), "text": text.replace("\r\n", "\n")})
		if not r.ok:
			return
	_load_characters()
	_load(str(_current.distillation_id))

func _target() -> Dictionary:
	var aliases: Array[String] = []
	for part in _aliases.text.replace("、", ",").replace("，", ",").split(",", false):
		if not part.strip_edges().is_empty():
			aliases.append(part.strip_edges())
	return {"name": _name.text.strip_edges(), "aliases": aliases}

func _preview_target() -> void:
	if _name.text.strip_edges().is_empty():
		_say("先填写蒸馏对象的名字。", true)
		return
	var r := await _call(_base() + ":preview", HTTPClient.METHOD_POST, _target())
	if r.ok:
		var p: Dictionary = r.data
		_preview.text = "素材里有 %d 段提到他（%d 字，共 %d 次），预计调用模型约 %d 次、输入约 %d 万 token。" % [
			p.relevant_segments, p.relevant_chars, p.mentions, p.estimated_calls,
			ceili(float(p.estimated_prompt_tokens) / 10000.0)]

func _start() -> void:
	if _name.text.strip_edges().is_empty():
		_say("先填写蒸馏对象的名字。", true)
		return
	var r := await _call(_base() + ":start", HTTPClient.METHOD_POST,
		{"target": _target(), "persona_id": _persona_id.text.strip_edges()})
	if r.ok:
		_current = r.data
		_refresh_picker_label()
		_render()

func _resume_run() -> void:
	var r := await _call(_base() + ":start", HTTPClient.METHOD_POST,
		{"target": _current.target, "persona_id": _current.persona_id, "language": _current.language})
	if r.ok:
		_current = r.data
		_render()

# ---- review -------------------------------------------------------------------------------

func _load_entries() -> void:
	var r := await _call(_base() + "/entries")
	if r.ok:
		_entries = r.data
		_entry_index = -1
		_fill_tree()
		_show_entry()

func _fill_tree() -> void:
	_tree.clear()
	var root := _tree.create_item()
	var counts := {}
	for layer in LAYERS:
		var group := _tree.create_item(root)
		group.set_text(0, "%s %s" % [layer, LAYER_NAMES[layer]])
		group.set_custom_font(0, UI.heading_font())
		group.set_selectable(0, false)
		for i in _entries.size():
			var e: Dictionary = _entries[i]
			if e.entry.layer != layer:
				continue
			var status := str(e.status)
			counts[status] = int(counts.get(status, 0)) + 1
			var item := _tree.create_item(group)
			var mark := " ！" if e.review_note != null and status == "proposed" else ""
			item.set_text(0, "［%s］%s · %s%s" % [STATUS_NAMES[status], CONSTRUCTS.get(e.entry.construct, ""), _short(str(e.entry.text)), mark])
			item.set_custom_color(0, STATUS_COLORS[status])
			item.set_metadata(0, i)
			if e.review_note != null:
				item.set_tooltip_text(0, "复核意见：" + str(e.review_note))
			if i == _entry_index:
				item.select(0)
	var parts: Array[String] = []
	for status in STATUS_NAMES:
		if counts.has(status):
			parts.append("%s %d" % [STATUS_NAMES[status], counts[status]])
	_counts.text = "共 %d 条：%s。带“！”的待审条目有复核意见。" % [_entries.size(), "，".join(PackedStringArray(parts))]

func _short(text: String, limit := 20) -> String:
	return text if text.length() <= limit else text.left(limit) + "…"

func _on_tree_selected() -> void:
	var item := _tree.get_selected()
	if item == null or item.get_metadata(0) == null:
		return
	_entry_index = int(item.get_metadata(0))
	_show_entry()

func _level(words: Array, v: float) -> String:
	return words[clampi(int(v * words.size()), 0, words.size() - 1)]

func _show_entry() -> void:
	_evidence.clear()
	_context.text = ""
	var has := _entry_index >= 0 and _entry_index < _entries.size()
	for node in [_entry_status, _strength_row, _text, _note_box, _evidence, _context]:
		node.visible = has
	if not has:
		_entry_title.text = "选择左侧条目"
		_entry_meta.text = "通过、驳回或直接改写；改写会保留原文和证据。"
		return
	var e: Dictionary = _entries[_entry_index]
	var entry: Dictionary = e.entry
	var status := str(e.status)
	var construct: String = CONSTRUCTS.get(entry.construct, "")
	_entry_title.text = construct if construct == LAYER_NAMES[entry.layer] else "%s · %s" % [LAYER_NAMES[entry.layer], construct]
	_entry_status.text = STATUS_NAMES[status]
	_entry_status.add_theme_color_override("font_color", STATUS_COLORS[status])
	var meta: Array[String] = [str(entry.construct_id)]
	if not entry.scope.is_empty():
		meta.append("适用：" + "、".join(PackedStringArray(entry.scope)))
	meta.append(str({"inference": "由素材推断", "configured": "人工设定"}.get(entry.source, entry.source)))
	_entry_meta.text = " · ".join(PackedStringArray(meta))
	var strength = entry.value.get("baseline") if entry.value is Dictionary else null
	_strength.visible = strength != null
	_strength_word.text = _level(LEVELS, float(strength)) if strength != null else "未标强度"
	if strength != null:
		_strength.set_value(float(strength))
	_sure.visible = entry.confidence != null
	_sure_word.text = _level(SURE, float(entry.confidence)) if entry.confidence != null else "—"
	if entry.confidence != null:
		_sure.set_value(float(entry.confidence))
	_text.text = str(entry.text)
	var notes: Array[String] = []
	if e.review_note != null:
		notes.append("复核意见：" + str(e.review_note))
	if e.original_text != null:
		notes.append("改写前：" + str(e.original_text))
	_note.text = "\n".join(PackedStringArray(notes))
	_note_box.visible = not notes.is_empty()
	for ev in e.evidence:
		_evidence.add_item("“%s”" % _short(str(ev.quote).replace("\n", " "), 56))
		_evidence.set_item_metadata(_evidence.item_count - 1, ev.evidence_id)
		_evidence.set_item_tooltip(_evidence.item_count - 1, str(ev.quote))
	_evidence.visible = not e.evidence.is_empty()
	_context.visible = not e.evidence.is_empty()

func _on_evidence_selected(index: int) -> void:
	var r := await _call(_base() + "/evidence/" + str(_evidence.get_item_metadata(index)))
	if not r.ok:
		return
	var ev: Dictionary = r.data
	var segment := str(ev.segment_text)
	var at := int(ev.quote_offset)
	var length := str(ev.quote).length()
	_context.text = "[color=#5f4630]%s · %s：%s[/color]\n\n%s[bgcolor=#f3d27a][b]%s[/b][/bgcolor]%s" % [
		ev.source, CONSTRUCTS.get(ev.construct, ""), _escape(str(ev.note)),
		_escape(segment.left(at)), _escape(segment.substr(at, length)), _escape(segment.substr(at + length))]
	# Scroll so the highlighted quote is in view.
	await get_tree().process_frame
	var line := _context.get_character_line(at + str(ev.note).length() + str(ev.source).length() + 8)
	_context.scroll_to_line(maxi(0, line - 2))

func _escape(text: String) -> String:
	return text.replace("[", "[lb]")

func _patch(body: Dictionary) -> void:
	if _entry_index < 0:
		return
	var e: Dictionary = _entries[_entry_index]
	var r := await _call(_base() + "/entries/" + str(e.entry.entry_id), HTTPClient.METHOD_PATCH, body)
	if r.ok:
		_entries[_entry_index] = r.data
		_fill_tree()
		_show_entry()

func _save_text() -> void:
	var text := _text.text.strip_edges()
	if text.is_empty():
		_say("条目内容不能为空。", true)
		return
	_patch({"text": text})

func _accept_pending() -> void:
	for i in _entries.size():
		if _entries[i].status == "proposed":
			var r := await _call(_base() + "/entries/" + str(_entries[i].entry.entry_id), HTTPClient.METHOD_PATCH, {"status": "accepted"})
			if not r.ok:
				break
			_entries[i] = r.data
	_fill_tree()
	_show_entry()

func _add_entry() -> void:
	var key := str(_add_construct.get_selected_metadata())
	var name_part := _add_id.text.strip_edges().to_lower().replace(" ", "_")
	if name_part.is_empty():
		name_part = "custom_%d" % (Time.get_unix_time_from_system() as int)
	var text := _add_text.text.strip_edges()
	if text.is_empty():
		_say("新增条目需要内容。", true)
		return
	var r := await _call(_base() + "/entries", HTTPClient.METHOD_POST, {
		"layer": CONSTRUCT_LAYER[key], "construct": key, "construct_id": "%s.%s" % [key, name_part],
		"text": text})
	if r.ok:
		_entries.append(r.data)
		_entry_index = _entries.size() - 1
		_add_id.text = ""
		_add_text.text = ""
		_fill_tree()
		_show_entry()

func _commit() -> void:
	var accepted := _entries.filter(func(e: Dictionary) -> bool: return e.status in ACCEPTED).size()
	var pending := _entries.filter(func(e: Dictionary) -> bool: return e.status == "proposed").size()
	if accepted == 0:
		_say("还没有通过的条目。", true)
		return
	var r := await _call(_base() + ":commit", HTTPClient.METHOD_POST, {})
	if r.ok:
		var result: Dictionary = r.data
		var before_id: String = app.persona_id
		var before_name: String = app.persona_name
		await app.switch_persona(str(result.persona_id), _display_name())
		_reviewing = false
		_deleted = false
		_done_loaded = ""
		await _load(str(_current.distillation_id))
		if not before_id.is_empty() and before_id != str(result.persona_id):
			_say("桌宠已经换成 %s。原来的 %s 还在，可以在右键「切换人格」里换回来。" % [
				_display_name(), before_name])
			return
		_say("已导入 %s：%d 条信念%s；%d 个构念暂无证据，保持未知。" % [
			_display_name(), result.entries,
			"（另有 %d 条待审未导入）" % pending if pending > 0 else "",
			result.unknown_constructs.size()])
