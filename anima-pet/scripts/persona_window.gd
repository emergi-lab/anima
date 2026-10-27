class_name PersonaWindow
extends Window
## 人格: the persona's three layers as they are now, its version history, and evolution —
## recent chats distilled into proposed changes that the owner reviews before they become the
## next version. Read-only on purpose: every change to the persona goes through an evolution
## (or a return to an earlier version), so each version has a reason.

const BASE_SIZE := Vector2i(1120, 780)
const MIN_SIZE := Vector2i(920, 640)
const POLL_SECONDS := 2.0
const TAB_EVOLVE := 3
const TAB_HISTORY := 4
const GROUPS := {
	"L3": ["overall_identity", "imago", "life_course"],
	"L2": ["need", "value", "goal", "strategy"],
	"L1": ["trait"],
}
## Plain names for the usual constructs; others show their own id.
const NAMES := {
	"overall_identity.self_view": "我怎么看自己",
	"imago.warrior": "战士", "imago.traveler": "旅人", "imago.sage": "智者", "imago.maker": "创造者",
	"imago.lover": "爱人", "imago.caregiver": "照顾者", "imago.friend": "朋友",
	"imago.ritual_keeper": "守礼的人",
	"life_course.turning_point": "人生转折", "life_course.origin": "出身与来历",
	"need.autonomy": "自己做主", "need.competence": "有本事", "need.relatedness": "和人亲近",
	"need.safety": "平安", "need.physiological": "吃饱睡好",
	"value.benevolence": "善待身边的人", "value.universalism": "公道与包容",
	"value.self_direction": "自己拿主意", "value.achievement": "有出息", "value.power": "地位与好处",
	"value.security": "安稳", "value.conformity": "守规矩", "value.tradition": "传统礼数",
	"value.hedonism": "享受", "value.stimulation": "新鲜刺激", "value.loyalty": "情义",
	"goal.current": "眼下的目标",
	"strategy.active_coping": "迎难而上", "strategy.planning": "先盘算再动",
	"strategy.instrumental_support": "找帮手", "strategy.avoidance": "躲开", "strategy.humor": "说笑化解",
}
## Big Five (BFI-2): domain → [name, English, facets in order as [id, name]].
const BIG_FIVE := {
	"extraversion": ["外向性", "Extraversion", [["sociability", "社交性"], ["assertiveness", "果断性"], ["energy_level", "活力"]]],
	"agreeableness": ["宜人性", "Agreeableness", [["compassion", "同情心"], ["respectfulness", "尊重他人"], ["trust", "信任"]]],
	"conscientiousness": ["尽责性", "Conscientiousness", [["organization", "条理性"], ["productiveness", "效率"], ["responsibility", "责任感"]]],
	"neuroticism": ["神经质", "Neuroticism", [["anxiety", "焦虑"], ["depression", "抑郁"], ["emotional_volatility", "情绪多变"]]],
	"openness": ["开放性", "Openness", [["intellectual_curiosity", "求知欲"], ["aesthetic_sensitivity", "审美敏感"], ["creative_imagination", "创造性想象"]]],
}
const TRAIT_LEVELS := ["很低", "较低", "中等", "较高", "很高"]
const ORIGINS := {"construction": "蒸馏", "evolution": "演化", "correction": "回到旧版本"}
const STEP_NAMES := {"extracting": "正在从聊天里找证据", "inducing": "正在和现有人格逐条比对", "reviewing": "正在复核"}
const LEVELS := ["很弱", "较弱", "一般", "较强", "很强"]

## The App autoload, injected on creation (a global name would not resolve in test scripts).
var app: Node
var _version: Dictionary = {}  # the active BeliefVersion
var _history: Array = []  # VersionHistory, newest first
var _changed_in: Dictionary = {}  # entry_id → k of the version that last changed it
var _preview: Dictionary = {}
var _evo: Dictionary = {}  # the unfinished evolution from new material, or {}
var _refl: Dictionary = {}  # the unfinished reflection, or {}
var _refl_preview: Dictionary = {}
var _material := false  # showing "用新素材演化" instead of the reflection
var _version_key := "B"  # the version shown
var _proposals: Array = []  # its ReviewedEntry list
var _materials: Array = []  # [{name, text}] to add when the evolution starts
var _poll := 0.0
var _busy := false

var _title: Label
var _version_label: Label
var _status: Label
var _portrait: AvatarView
var _tabs: TabContainer
var _layers: Dictionary = {}  # layer → VBoxContainer
var _history_box: VBoxContainer
var _evolve_box: VBoxContainer
var _files: FileDialog
var _confirm: ConfirmationDialog
var _confirm_action: Callable

func _init() -> void:
	title = "Anima · 人格"
	size = BASE_SIZE
	min_size = MIN_SIZE
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
	var root := VBoxContainer.new()
	root.add_theme_constant_override("separation", 10)
	margin.add_child(root)
	root.add_child(_build_header())
	_tabs = TabContainer.new()
	_tabs.size_flags_vertical = Control.SIZE_EXPAND_FILL
	root.add_child(_tabs)
	for layer in ["L3", "L2", "L1"]:
		var box := VBoxContainer.new()
		_layers[layer] = box
		_tabs.add_child(_scroll(box))
	_evolve_box = VBoxContainer.new()
	_tabs.add_child(_scroll(_evolve_box))
	_history_box = VBoxContainer.new()
	_tabs.add_child(_scroll(_history_box))
	for i in 5:
		_tabs.set_tab_title(i, ["叙事身份", "特征适应", "特质（大五人格）", "反思", "演化历史"][i])
	_build_dialogs()
	app.look_changed.connect(_refresh_portrait)
	app.evolution_updated.connect(_on_evolution_updated)
	app.reflection_updated.connect(func(d: Dictionary) -> void:
		if visible and str(d.reflection_id) == str(_refl.get("reflection_id", "")):
			_refl = d
			_render_evolve())

func _process(delta: float) -> void:
	if not visible or _evo.get("stage", "") != "running":
		return
	_poll += delta
	if _poll >= POLL_SECONDS and not _busy:
		_poll = 0.0
		_load_evolution(str(_evo.distillation_id))

# ---- layout ---------------------------------------------------------------------------------

func _build_header() -> HBoxContainer:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 12)
	_portrait = AvatarView.new()
	_portrait.pixel_scale = 2
	_portrait.show_name = false
	row.add_child(_portrait)
	var col := VBoxContainer.new()
	col.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	col.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(col)
	_title = UI.pixel_label("人格", 26)
	col.add_child(_title)
	_version_label = UI.label("", 14, UI.C_MUTED)
	col.add_child(_version_label)
	_status = UI.label("", 14, UI.C_MUTED)
	_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	col.add_child(_status)
	var evolve := _button("反思…", func() -> void: _tabs.current_tab = TAB_EVOLVE)
	evolve.tooltip_text = "让它回顾最近的聊天，想想自己有没有变"
	evolve.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(evolve)
	var refresh := _button("刷新", reload)
	refresh.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	row.add_child(refresh)
	return row

func _build_dialogs() -> void:
	_files = FileDialog.new()
	_files.use_native_dialog = true
	_files.access = FileDialog.ACCESS_FILESYSTEM
	_files.file_mode = FileDialog.FILE_MODE_OPEN_FILES
	_files.filters = PackedStringArray(["*.txt, *.md ; 文本（UTF-8）"])
	_files.files_selected.connect(_add_files)
	add_child(_files)
	_confirm = ConfirmationDialog.new()
	_confirm.ok_button_text = "确定"
	_confirm.cancel_button_text = "取消"
	_confirm.confirmed.connect(func() -> void: _confirm_action.call())
	add_child(_confirm)

func _scroll(content: VBoxContainer) -> ScrollContainer:
	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	content.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	content.add_theme_constant_override("separation", 10)
	scroll.add_child(content)
	return scroll

# ---- data -----------------------------------------------------------------------------------

func _base() -> String:
	return "/v1/personas/" + app.persona_id.uri_encode()

func _call(path: String, method := HTTPClient.METHOD_GET, body: Variant = null,
		timeout := 120.0) -> Dictionary:
	_busy = true
	var r: Dictionary = await app.api.request_json(path, method, body, timeout)
	_busy = false
	if not r.ok and app.model_trouble(r):
		_say(app.model_line(r), true)
		app.remind_model(self, r)
	elif not r.ok:
		_say(str(r.error), true)
	return r

func _say(text: String, error := false) -> void:
	_status.text = text
	_status.add_theme_color_override("font_color", UI.C_ERROR if error else UI.C_MUTED)

func reload() -> void:
	_refresh_portrait()
	if app.persona_id.is_empty():
		_title.text = "人格"
		_say("还没有人格。先在蒸馏工作台蒸馏并导入一个。")
		return
	_title.text = "%s的人格" % app.persona_name
	_say("读取中…")
	var h := await _call(_base() + "/history")
	if not h.ok:
		return
	_history = h.data
	var v := await _call(_base() + "/versions/" + str(_history[0].version_id))
	if not v.ok:
		return
	_version = v.data
	_changed_in.clear()
	for i in range(_history.size() - 1, -1, -1):  # oldest first, so later versions win
		for ch in _history[i].changes:
			_changed_in[str(ch.entry_id)] = int(_history[i].k)
	var latest: Dictionary = _history[0]
	_version_label.text = "当前版本 B%d · %s · %s · 共 %d 条" % [int(latest.k),
		ORIGINS.get(latest.origin, latest.origin), UI.local_time(latest.committed_at),
		_known(_version.entries).size()]
	_say("")
	for layer in _layers:
		_fill_layer(layer)
	_fill_history()
	await _load_preview()

func _refresh_portrait() -> void:
	_portrait.seed_text = app.persona_id
	_portrait.spec = app.look()

func _known(entries: Array) -> Array:
	return entries.filter(func(e: Dictionary) -> bool: return e.epistemic_status != "unknown")

static func name_of(construct_id: String) -> String:
	var parts := construct_id.split(".")
	if parts.size() == 3 and parts[0] == "trait" and BIG_FIVE.has(parts[1]):
		var domain: Array = BIG_FIVE[parts[1]]
		for f in domain[2]:
			if f[0] == parts[2]:
				return "%s · %s" % [domain[0], f[1]]
		return "%s · %s" % [domain[0], parts[2]]
	return NAMES.get(construct_id, parts[parts.size() - 1])

static func level(v: float) -> String:
	return LEVELS[clampi(int(v * 5.0), 0, 4)]

# ---- the three layers -----------------------------------------------------------------------

func _fill_layer(layer: String) -> void:
	var box: VBoxContainer = _layers[layer]
	_clear(box)
	var entries: Array = _version.entries.filter(func(e: Dictionary) -> bool: return e.layer == layer)
	var unknown: Array = []
	if layer == "L1":
		_fill_big_five(box, entries)
		return
	for category in GROUPS[layer]:
		var mine: Array = entries.filter(func(e: Dictionary) -> bool: return e.construct == category)
		var known: Array = _known(mine)
		if known.is_empty():
			if not mine.is_empty():
				unknown.append(Workbench.CONSTRUCTS[category])
			continue
		box.add_child(UI.heading(Workbench.CONSTRUCTS[category], 19))
		for e in known:
			box.add_child(_entry_card(e))
	if not unknown.is_empty():
		var l := UI.label("还不了解：%s（素材里没有相关的内容）" % "、".join(unknown), 14, UI.C_MUTED)
		l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		box.add_child(l)
	if box.get_child_count() == 0:
		box.add_child(UI.label("这一层还是空的。", 15, UI.C_MUTED))

## Traits as the Big Five: an overview of the five domains (mean of the facets the persona has),
## then each domain with its facet cards and the facets the material said nothing about.
func _fill_big_five(box: VBoxContainer, entries: Array) -> void:
	var by_domain: Dictionary = {}
	for e in _known(entries):
		var d := str(e.construct_id).get_slice(".", 1)
		if not by_domain.has(d):
			by_domain[d] = []
		by_domain[d].append(e)
	var overview := _card(box, "大五人格")
	overview.add_child(_wrapped("五个维度各由三个分面组成。条形是这个人物已知分面的平均强弱；素材里没有体现的分面不计入。", 13, UI.C_MUTED))
	var grid := GridContainer.new()
	grid.columns = 4
	grid.add_theme_constant_override("h_separation", 14)
	grid.add_theme_constant_override("v_separation", 8)
	overview.add_child(grid)
	for d in BIG_FIVE:
		var info: Array = BIG_FIVE[d]
		var mine: Array = by_domain.get(d, [])
		grid.add_child(UI.heading("%s" % info[0], 16))
		var values: Array = mine.map(func(e: Dictionary) -> float: return _baseline(e)).filter(func(v: float) -> bool: return v >= 0.0)
		var bar := Workbench.Bar.new()
		bar.custom_minimum_size.x = 260
		var word := "未知"
		if not values.is_empty():
			var mean: float = values.reduce(func(a: float, v: float) -> float: return a + v, 0.0) / values.size()
			bar.set_value(mean)
			word = TRAIT_LEVELS[clampi(int(mean * 5.0), 0, 4)]
		grid.add_child(bar)
		grid.add_child(UI.label(word, 14))
		grid.add_child(UI.label("已知 %d/3 个分面" % _facets_known(mine), 13, UI.C_MUTED))
	for d in BIG_FIVE:
		var info: Array = BIG_FIVE[d]
		box.add_child(UI.heading("%s %s" % [info[0], info[1]], 19))
		var mine: Array = by_domain.get(d, [])
		var missing := PackedStringArray()
		for f in info[2]:
			var hit := mine.filter(func(e: Dictionary) -> bool: return str(e.construct_id).get_slice(".", 2) == f[0])
			if hit.is_empty():
				missing.append(f[1])
			for e in hit:
				box.add_child(_entry_card(e))
		var extra := mine.filter(func(e: Dictionary) -> bool:
			return not info[2].any(func(f: Array) -> bool: return str(e.construct_id).get_slice(".", 2) == f[0]))
		for e in extra:
			box.add_child(_entry_card(e))
		if not missing.is_empty():
			box.add_child(_wrapped("素材里没有体现：%s" % "、".join(missing), 14, UI.C_MUTED))
	var other: Array = _known(entries).filter(func(e: Dictionary) -> bool: return not BIG_FIVE.has(str(e.construct_id).get_slice(".", 1)))
	if not other.is_empty():
		box.add_child(UI.heading("其他特质", 19))
		for e in other:
			box.add_child(_entry_card(e))

func _facets_known(mine: Array) -> int:
	var seen := {}
	for e in mine:
		seen[str(e.construct_id).get_slice(".", 2)] = true
	return seen.size()

static func _baseline(e: Dictionary) -> float:
	var v: Variant = e.get("value")
	if typeof(v) == TYPE_DICTIONARY and (v as Dictionary).has("baseline"):
		return float(v.baseline)
	return -1.0

func _entry_card(e: Dictionary) -> Control:
	var panel := PanelContainer.new()
	panel.add_theme_stylebox_override("panel", _card_box(UI.C_SURFACE))
	var col := VBoxContainer.new()
	col.add_theme_constant_override("separation", 6)
	panel.add_child(col)
	var head := HBoxContainer.new()
	head.add_theme_constant_override("separation", 8)
	col.add_child(head)
	var title := UI.heading(name_of(str(e.construct_id)), 17)
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	head.add_child(title)
	if _changed_in.has(str(e.entry_id)):
		head.add_child(_chip("B%d 演化" % _changed_in[str(e.entry_id)], Color("#1f5578")))
	elif e.source == "configured":
		head.add_child(_chip("你添加的", Color("#7a4f00")))
	else:
		head.add_child(_chip("蒸馏", UI.C_MUTED))
	var refs: Array = e.get("evidence_refs", [])
	head.add_child(_chip("有 %d 处原文" % refs.size() if not refs.is_empty() else "推断", UI.C_MUTED))
	var bars := _value_row(e.get("value"), e.layer == "L1")
	if bars != null:
		col.add_child(bars)
	col.add_child(_wrapped(str(e.text), 15))
	var scope: Array = e.get("scope", [])
	if not scope.is_empty():
		col.add_child(_wrapped("适用：" + "、".join(scope), 13, UI.C_MUTED))
	if not refs.is_empty():
		var quotes := _wrapped("", 14, UI.C_MUTED)
		quotes.visible = false
		var toggle := Button.new()
		toggle.flat = true
		toggle.text = "看依据 ▸"
		toggle.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
		toggle.add_theme_color_override("font_color", UI.C_PRIMARY)
		toggle.pressed.connect(func() -> void: _toggle_quotes(toggle, quotes, refs))
		col.add_child(toggle)
		col.add_child(quotes)
	return panel

func _value_row(value: Variant, is_trait := false) -> Control:
	if typeof(value) != TYPE_DICTIONARY:
		return null
	var v: Dictionary = value
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	var pairs: Array = []
	if v.has("satisfaction"):
		pairs = [["满足", v.satisfaction], ["受挫", v.get("frustration", 0.0)]]
	elif v.has("baseline"):
		pairs = [["强弱", v.baseline]]
	if pairs.is_empty():
		return null
	for p in pairs:
		row.add_child(UI.label(str(p[0]), 13, UI.C_MUTED))
		var bar := Workbench.Bar.new()
		bar.set_value(float(p[1]))
		row.add_child(bar)
		row.add_child(UI.label(TRAIT_LEVELS[clampi(int(float(p[1]) * 5.0), 0, 4)] if is_trait else level(float(p[1])), 13))
	return row

func _toggle_quotes(toggle: Button, quotes: Label, refs: Array) -> void:
	quotes.visible = not quotes.visible
	toggle.text = "收起依据 ▾" if quotes.visible else "看依据 ▸"
	if not quotes.visible or not quotes.text.is_empty():
		return
	quotes.text = "读取中…"
	var shown: Array = refs.slice(0, 8)
	var r := await _call(_base() + "/materials?refs=" + ",".join(shown).uri_encode())
	if not r.ok:
		quotes.text = "读不到依据。"
		return
	var lines := PackedStringArray()
	for ref in shown:
		if r.data.has(ref):
			lines.append("「%s」" % str(r.data[ref]))
	if refs.size() > shown.size():
		lines.append("……还有 %d 处" % (refs.size() - shown.size()))
	quotes.text = "\n".join(lines) if not lines.is_empty() else "这些原文没有保存下来。"

# ---- history --------------------------------------------------------------------------------

func _fill_history() -> void:
	_clear(_history_box)
	var r := await _call(_base() + "/reflections?limit=50")
	var reflections: Array = r.data if r.ok else []
	for h in _history:
		var panel := PanelContainer.new()
		panel.add_theme_stylebox_override("panel", _card_box(UI.C_SURFACE))
		var col := VBoxContainer.new()
		col.add_theme_constant_override("separation", 6)
		panel.add_child(col)
		var head := HBoxContainer.new()
		col.add_child(head)
		var what := "从素材蒸馏出 %d 条" % int(h.entries) if h.origin == "construction" \
			else "改了 %d 条" % h.changes.size()
		var title := UI.heading("B%d · %s · %s" % [int(h.k), ORIGINS.get(h.origin, h.origin), what], 17)
		title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		head.add_child(title)
		head.add_child(UI.label(UI.local_time(h.committed_at), 13, UI.C_MUTED))
		if h.version_id == _version.get("version_id", ""):
			head.add_child(_chip("当前", Color("#2f6b2a")))
		else:
			var back := _button("回到这个版本", func() -> void: _ask_restore(h))
			head.add_child(back)
		var why: Array = reflections.filter(func(x: Dictionary) -> bool:
			return x.get("committed_version_id") == h.version_id)
		if not why.is_empty():  # this version came out of a reflection
			col.add_child(_wrapped("来自反思，采用了版本 %s：" % why[0].chosen, 14, UI.C_PRIMARY))
			col.add_child(_quote_box("它的反思", str(why[0].diary), UI.C_MUTED))
		for ch in h.changes:
			col.add_child(_change_view(ch.get("before"), ch.get("after")))
		_history_box.add_child(panel)
	var other: Array = reflections.filter(func(x: Dictionary) -> bool:
		return x.status in ["declined", "ready", "failed"])
	if not other.is_empty():
		_history_box.add_child(UI.heading("其他反思", 18))
	for x in other:
		var panel := PanelContainer.new()
		panel.add_theme_stylebox_override("panel", _card_box(UI.C_SURFACE))
		var col := VBoxContainer.new()
		col.add_theme_constant_override("separation", 6)
		panel.add_child(col)
		var state: String = {"declined": "没有采用，保持原样", "ready": "等你审批",
			"failed": "中断了"}.get(x.status, x.status)
		col.add_child(UI.heading("反思 · %s · %s" % [state, UI.local_time(x.created_at)], 16))
		if x.get("diary") != null:
			col.add_child(_quote_box("", str(x.diary), UI.C_MUTED))
		_history_box.add_child(panel)

func _ask_restore(h: Dictionary) -> void:
	_confirm.title = "回到 B%d" % int(h.k)
	_confirm.dialog_text = "把 B%d 的人格复制成一个新版本，作为当前人格。\n现在的版本会保留在历史里，随时还能回来。\n正在进行的聊天会先结束。" % int(h.k)
	_confirm_action = func() -> void: _restore(str(h.version_id))
	app.popup_dialog(_confirm, Vector2i(480, 200), self)

func _restore(version_id: String) -> void:
	var r := await _call(_base() + "/versions/%s:restore" % version_id, HTTPClient.METHOD_POST, {})
	if r.ok:
		app.persona_changed.emit("我好像回到了从前的样子。")
		await reload()
		_say("已回到旧版本，成为 B%d。" % int(r.data.k))

## Before → after of one entry: text and strength, for history and evolution proposals.
func _change_view(before: Variant, after: Variant) -> Control:
	var col := VBoxContainer.new()
	col.add_theme_constant_override("separation", 4)
	var any: Dictionary = after if typeof(after) == TYPE_DICTIONARY else before
	var kind := "新增" if typeof(before) != TYPE_DICTIONARY else ("移除" if typeof(after) != TYPE_DICTIONARY else "改写")
	if kind == "改写" and str(after.text).begins_with(str(before.text)):
		kind = "追加"
	var layer_name: String = Workbench.LAYER_NAMES.get(any.layer, any.layer)
	col.add_child(UI.label("%s · %s · %s" % [layer_name, name_of(str(any.construct_id)), kind], 15, UI.C_PRIMARY))
	if typeof(before) == TYPE_DICTIONARY and typeof(after) == TYPE_DICTIONARY:
		var moved := _value_change(before.get("value"), after.get("value"))
		if not moved.is_empty():
			col.add_child(UI.label(moved, 14))
		if str(before.text) != str(after.text):
			col.add_child(_quote_box("原来", str(before.text), UI.C_MUTED))
			col.add_child(_quote_box("现在", str(after.text), UI.C_TEXT))
	else:
		col.add_child(_quote_box("", str(any.text), UI.C_TEXT))
	return col

func _value_change(a: Variant, b: Variant) -> String:
	if typeof(a) != TYPE_DICTIONARY or typeof(b) != TYPE_DICTIONARY:
		return ""
	var parts := PackedStringArray()
	for key in [["baseline", "强弱"], ["satisfaction", "满足"], ["frustration", "受挫"]]:
		if a.has(key[0]) and b.has(key[0]) and not is_equal_approx(float(a[key[0]]), float(b[key[0]])):
			var from := float(a[key[0]])
			var to := float(b[key[0]])
			var word := "%s → %s" % [level(from), level(to)] if level(from) != level(to) \
				else ("%s，略强一点" if to > from else "%s，略弱一点") % level(to)
			parts.append("%s：%s" % [key[1], word])
	return "；".join(parts)

func _quote_box(label: String, text: String, color: Color) -> Control:
	var panel := PanelContainer.new()
	panel.add_theme_stylebox_override("panel", _note_style(UI.C_INPUT, Color(UI.C_FRAME, 0.5)))
	var l := _wrapped((label + "：" if not label.is_empty() else "") + text, 14, color)
	panel.add_child(l)
	return panel

# ---- evolution ------------------------------------------------------------------------------

func _load_preview() -> void:
	var name_q: String = "?persona_name=" + app.persona_name.uri_encode()
	var rp := await _call(_base() + "/reflection:preview" + name_q)
	if rp.ok:
		_refl_preview = rp.data
		_refl = {}
		if rp.data.get("open") != null:
			var got := await _call("/v1/reflections/" + str(rp.data.open))
			if got.ok:
				_refl = got.data
				if _refl.status == "running":
					app.watch_reflection(str(_refl.reflection_id))
	var r := await _call(_base() + "/evolution:preview" + name_q)
	if not r.ok:
		return
	_preview = r.data
	if _preview.get("running") != null:
		_material = true
		await _load_evolution(str(_preview.running))
	else:
		_evo = {}
		_render_evolve()

func _load_evolution(id: String) -> void:
	var r := await _call("/v1/distillations/" + id)
	if not r.ok:
		return
	var was := str(_evo.get("stage", ""))
	_evo = r.data
	if _evo.stage == "running":
		app.watch_evolution(id)
	if _evo.stage == "review" and (was != "review" or _proposals.is_empty()):
		var e := await _call("/v1/distillations/%s/entries" % id)
		_proposals = e.data if e.ok else []
	_render_evolve()

func _on_evolution_updated(d: Dictionary) -> void:
	if visible and str(d.get("distillation_id", "")) == str(_evo.get("distillation_id", "")):
		await _load_evolution(str(d.distillation_id))

func _render_evolve() -> void:
	_clear(_evolve_box)
	if not _material:
		_render_reflection()
		return
	if _evo.is_empty() or str(_evo.get("stage", "")) == "draft":
		var back := LinkButton.new()
		back.text = "← 回到反思"
		back.add_theme_color_override("font_color", UI.C_PRIMARY)
		back.pressed.connect(func() -> void:
			_material = false
			_render_evolve())
		_evolve_box.add_child(back)
	var stage := str(_evo.get("stage", ""))
	match stage:
		"":
			_render_start()
		"draft":
			_render_start()
		"running":
			_render_running()
		"failed":
			_render_failed()
		"review":
			_render_review()

# ---- reflection -----------------------------------------------------------------------------

func _render_reflection() -> void:
	match str(_refl.get("status", "")):
		"running":
			var card := _card(_evolve_box, "反思中")
			card.add_child(UI.label("正在回顾最近的聊天，想想自己……", 17))
			card.add_child(_wrapped("大约二十秒。可以关掉这个窗口，想好了它会告诉你。", 14, UI.C_MUTED))
		"ready":
			_render_reflection_ready()
		"failed":
			var card := _card(_evolve_box, "反思中断了")
			card.add_child(_wrapped(str(_refl.get("error", "")), 15, UI.C_ERROR))
			var row := HBoxContainer.new()
			row.add_theme_constant_override("separation", 10)
			card.add_child(row)
			if app.model_trouble(_refl.get("error", "")):
				row.add_child(_button("去设置", app.open_model_settings))
			row.add_child(_button("再反思一次", func() -> void:
				await _decline_reflection()
				_start_reflection()))
			row.add_child(_button("算了", _decline_reflection))
		_:
			_render_reflection_start()

func _render_reflection_start() -> void:
	var card := _card(_evolve_box, "反思：回顾最近的经历")
	card.add_child(_wrapped("它会读你们最近的聊天和记忆，用自己的话写一段反思，说说注意到了什么；再据此拟出几个不同程度的演化版本（微调 / 适度 / 明显）。每处改动都要有你说过的话作依据，它自己说的话不算。你审批采用哪个版本，或者都不采用——不经你同意，它不会改变。", 15))
	var p := _refl_preview
	if not p.is_empty():
		card.add_child(_wrapped("自 B%d（%s）以来：%d 段聊天，你说了 %d 句；反复出现的言行偏差 %d 处。" % [
			int(p.since_version), UI.local_time(p.since), int(p.sessions), int(p.owner_lines),
			int(p.deviations)], 15))
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 12)
	card.add_child(row)
	var go := _button("开始反思", _start_reflection)
	go.disabled = p.is_empty() or not bool(p.enough)
	row.add_child(go)
	var hint := UI.label("约 2 次模型调用、二十秒左右。" if not go.disabled
		else "还没有可以回顾的聊天：多和它聊聊，再来让它反思。", 14, UI.C_MUTED)
	hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	hint.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(hint)
	var material := LinkButton.new()
	material.text = "用新素材演化…"
	material.add_theme_color_override("font_color", UI.C_PRIMARY)
	material.pressed.connect(func() -> void:
		_material = true
		_render_evolve())
	_evolve_box.add_child(material)

func _start_reflection() -> void:
	var r := await _call(_base() + "/reflections", HTTPClient.METHOD_POST,
		{"persona_name": app.persona_name})
	if not r.ok:
		return
	_refl = r.data
	_version_key = "B"
	app.watch_reflection(str(_refl.reflection_id))
	_render_evolve()

func _render_reflection_ready() -> void:
	var diary := _card(_evolve_box, "%s的反思" % app.persona_name)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 12)
	diary.add_child(row)
	var icon := TextureRect.new()
	icon.texture = app.persona_icon(app.persona_id, 3)
	icon.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
	icon.stretch_mode = TextureRect.STRETCH_KEEP_CENTERED
	icon.custom_minimum_size = Vector2(96, 66)
	icon.size_flags_vertical = Control.SIZE_SHRINK_BEGIN
	row.add_child(icon)
	row.add_child(_quote_box("", str(_refl.diary), UI.C_TEXT))
	row.get_child(1).size_flags_horizontal = Control.SIZE_EXPAND_FILL
	if not _refl.findings.is_empty():
		diary.add_child(UI.heading("它注意到的", 16))
		for f in _refl.findings:
			diary.add_child(_wrapped("· %s —— %s" % [f.what, f.meaning], 14))
	var versions: Array = _refl.versions
	if versions.is_empty():
		var none := _card(_evolve_box, "这次没有需要改变的地方")
		none.add_child(_wrapped("回顾下来，没有足够清楚的依据说明它该怎样变。保持原样就好。", 15))
		var ok := _button("知道了", _decline_reflection)
		ok.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
		none.add_child(ok)
		return
	if not versions.any(func(v: Dictionary) -> bool: return v.key == _version_key):
		_version_key = str(versions[0].key)
	var pick := _card(_evolve_box, "它拟出的演化版本：请你审批")
	var tabs := HBoxContainer.new()
	tabs.add_theme_constant_override("separation", 8)
	pick.add_child(tabs)
	for v in versions:
		var b := Button.new()
		b.text = "版本 %s · %s（%d 处）" % [v.key, v.title, v.changes.size()]
		b.toggle_mode = true
		b.button_pressed = v.key == _version_key
		var key := str(v.key)
		b.pressed.connect(func() -> void:
			_version_key = key
			_render_evolve())
		tabs.add_child(b)
	var v: Dictionary = versions.filter(func(x: Dictionary) -> bool: return x.key == _version_key)[0]
	pick.add_child(_wrapped(str(v.summary), 16, UI.C_ME))
	for ch in v.changes:
		var panel := PanelContainer.new()
		panel.add_theme_stylebox_override("panel", _card_box(UI.C_INPUT))
		var col := VBoxContainer.new()
		col.add_theme_constant_override("separation", 6)
		panel.add_child(col)
		col.add_child(_change_view(ch.get("before"), ch.after))
		col.add_child(_wrapped("为什么：" + str(ch.reason), 14, UI.C_ME))
		var lines := PackedStringArray()
		for e in ch.evidence:
			lines.append("%s：「%s」" % [e.who, e.text])
		if not lines.is_empty():
			col.add_child(_wrapped("依据：" + "  ".join(lines), 13, UI.C_MUTED))
		pick.add_child(panel)
	var tried := _wrapped("", 14)
	tried.visible = false
	pick.add_child(tried)
	var actions := HBoxContainer.new()
	actions.add_theme_constant_override("separation", 10)
	pick.add_child(actions)
	var try_btn := _button("试答对比", func() -> void: pass)
	try_btn.tooltip_text = "现在的它和这个版本的它回答同一个问题（约 30 秒）"
	try_btn.pressed.connect(func() -> void: _try_version(str(v.key), try_btn, tried))
	actions.add_child(try_btn)
	var approve := _button("采用版本 %s…" % v.key, func() -> void: _ask_approve(v))
	actions.add_child(approve)
	var spacer := Control.new()
	spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	actions.add_child(spacer)
	actions.add_child(_button("都不采用", _ask_decline_reflection))

func _try_version(key: String, btn: Button, out: Label) -> void:
	btn.disabled = true
	btn.text = "试答中…"
	out.visible = true
	out.text = "两个版本正在回答同一个问题……"
	var r := await _call("/v1/reflections/%s/versions/%s:try" % [_refl.reflection_id, key],
		HTTPClient.METHOD_POST, {}, 180.0)
	btn.disabled = false
	btn.text = "再试一次"
	out.text = "没有试成：" + str(r.error) if not r.ok else \
		"问：%s\n现在的它：%s\n版本 %s 的它：%s" % [r.data.situation, r.data.before, key, r.data.after]

func _ask_approve(v: Dictionary) -> void:
	_confirm.title = "审批：采用版本 %s" % v.key
	_confirm.dialog_text = "采用「版本 %s · %s」？\n%d 处改动会成为%s的新版本；旧版本保留在演化历史里，随时可以回去。\n正在进行的聊天会先结束。" % [
		v.key, v.title, v.changes.size(), app.persona_name]
	_confirm_action = func() -> void: _approve(str(v.key))
	app.popup_dialog(_confirm, Vector2i(520, 220), self)

func _approve(key: String) -> void:
	var r := await _call("/v1/reflections/%s:approve" % _refl.reflection_id,
		HTTPClient.METHOD_POST, {"version": key})
	if not r.ok:
		return
	_refl = {}
	app.persona_changed.emit("我想通了一些事，好像有点不一样了。")
	await reload()
	_tabs.current_tab = TAB_HISTORY
	_say("已采用版本 %s。" % key)

func _ask_decline_reflection() -> void:
	_confirm.title = "都不采用"
	_confirm.dialog_text = "这次的几个版本都不采用，人格保持原样。\n它的反思日记会留在演化历史里。"
	_confirm_action = _decline_reflection
	app.popup_dialog(_confirm, Vector2i(460, 170), self)

func _decline_reflection() -> void:
	if _refl.is_empty():
		return
	var r := await _call("/v1/reflections/%s:decline" % _refl.reflection_id, HTTPClient.METHOD_POST, {})
	if r.ok:
		_refl = {}
		await reload()

func _render_start() -> void:
	var card := _card(_evolve_box, "用新素材演化")
	card.add_child(_wrapped("加入新素材（比如原著的后续章节），连同最近的聊天一起再蒸馏一次，给出逐条的修改建议。每次只走一小步：强弱只微调一点，叙事身份只追加带条件的描述。你逐条审阅，确认后才会成为新版本。", 15))
	if _preview.is_empty():
		card.add_child(UI.label("读取中…", 15, UI.C_MUTED))
		return
	var p := _preview
	var since := "自 B%d（%s）以来：" % [int(p.since_version), UI.local_time(p.since)]
	var facts := "%d 段聊天，你说了 %d 句，它说了 %d 句；它察觉到 %d 处反复出现的言行偏差。" % [
		int(p.sessions), int(p.owner_lines), int(p.persona_lines), int(p.deviations)]
	card.add_child(_wrapped(since + facts, 15))
	var mats := _card(_evolve_box, "额外素材（可选）")
	mats.add_child(_wrapped("比如原著的后续章节、你写的关于它的设定。素材里的言行都可以作为依据。", 14, UI.C_MUTED))
	for m in _materials:
		mats.add_child(UI.label("· %s（%d 字）" % [m.name, str(m.text).length()], 14))
	var add := _button("添加素材文件…", func() -> void: app.popup_dialog(_files, Vector2i(820, 560), self))
	add.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	mats.add_child(add)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 10)
	_evolve_box.add_child(row)
	var ready_to_go: bool = bool(p.enough) or not _materials.is_empty()
	var go := _button("开始演化", _start_evolution)
	go.disabled = not ready_to_go
	row.add_child(go)
	var hint := UI.label("预计调用约 %d 次模型，1–3 分钟。" % int(p.estimated_calls) if ready_to_go
		else "近期没有足够的变化依据：多和它聊聊，或者告诉它你对它的看法，再来试试。", 14, UI.C_MUTED)
	hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	hint.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(hint)
	if str(_evo.get("stage", "")) == "draft":
		row.add_child(_button("放弃", _discard))

func _add_files(paths: PackedStringArray) -> void:
	for path in paths:
		var text := FileAccess.get_file_as_string(path)
		if text.is_empty():
			_say("读不到 %s：需要 UTF-8 编码的文本文件。" % path.get_file(), true)
			continue
		_materials.append({"name": path.get_file(), "text": text.replace("\r\n", "\n")})
	_render_evolve()

func _start_evolution() -> void:
	var id := str(_evo.get("distillation_id", ""))
	if id.is_empty():
		var r := await _call(_base() + "/evolutions", HTTPClient.METHOD_POST,
			{"persona_name": app.persona_name})
		if not r.ok:
			return
		id = str(r.data.distillation_id)
		_evo = r.data
	for m in _materials:
		var s := await _call("/v1/distillations/%s/sources" % id, HTTPClient.METHOD_POST, m)
		if not s.ok:
			return
	_materials.clear()
	var r := await _call("/v1/distillations/%s:start" % id, HTTPClient.METHOD_POST,
		{"target": {"name": app.persona_name, "aliases": []}, "persona_id": app.persona_id,
		 "language": "zh-CN"})
	if not r.ok:
		await _load_evolution(id)
		return
	_say("")
	_evo = r.data
	app.watch_evolution(id)
	_render_evolve()

func _render_running() -> void:
	var card := _card(_evolve_box, "演化中")
	var p: Dictionary = _evo.progress
	var step := str(_evo.get("step", ""))
	card.add_child(UI.label(STEP_NAMES.get(step, "准备中"), 17))
	var bar := ProgressBar.new()
	bar.custom_minimum_size.y = 18
	bar.show_percentage = false
	var relevant := maxi(int(p.relevant), 1)
	bar.value = 100.0 * int(p.extracted) / relevant * 0.6 if step == "extracting" \
		else (75.0 if step == "inducing" else 92.0)
	card.add_child(bar)
	card.add_child(UI.label("找到 %d 条依据 · 已调用模型 %d 次" % [int(p.evidence), int(_evo.usage.model_calls)], 14, UI.C_MUTED))
	card.add_child(_wrapped("可以关掉这个窗口。好了它会告诉你。", 14, UI.C_MUTED))

func _render_failed() -> void:
	var card := _card(_evolve_box, "演化中断了")
	card.add_child(_wrapped(str(_evo.get("error", "")), 15, UI.C_ERROR))
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 10)
	card.add_child(row)
	if app.model_trouble(_evo.get("error", "")):
		row.add_child(_button("去设置", app.open_model_settings))
	row.add_child(_button("再试一次", _start_evolution))
	row.add_child(_button("放弃这次演化", _discard))

func _render_review() -> void:
	if _proposals.is_empty():
		var card := _card(_evolve_box, "这次没有需要改变的地方")
		card.add_child(_wrapped("最近的经历里，没有足够清楚的依据说明它该怎样变。保持原样就好。", 15))
		if _evo.get("error") != null:
			card.add_child(_wrapped(str(_evo.error), 14, UI.C_MUTED))
		var done := _button("结束这次演化", _discard)
		done.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
		card.add_child(done)
		return
	var accepted := _proposals.filter(func(e: Dictionary) -> bool: return e.status in Workbench.ACCEPTED).size()
	var head := HBoxContainer.new()
	head.add_theme_constant_override("separation", 10)
	_evolve_box.add_child(head)
	var count := UI.heading("%d 条修改建议，已接受 %d 条" % [_proposals.size(), accepted], 18)
	count.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	head.add_child(count)
	head.add_child(_button("放弃这次演化", _ask_discard))
	var commit := _button("确认演化（%d 条）" % accepted, _ask_commit)
	commit.disabled = accepted == 0
	head.add_child(commit)
	for e in _proposals:
		_evolve_box.add_child(_proposal_card(e))

func _proposal_card(p: Dictionary) -> Control:
	var e: Dictionary = p.entry
	var panel := PanelContainer.new()
	panel.add_theme_stylebox_override("panel", _card_box(UI.C_SURFACE))
	var col := VBoxContainer.new()
	col.add_theme_constant_override("separation", 6)
	panel.add_child(col)
	var head := HBoxContainer.new()
	head.add_theme_constant_override("separation", 8)
	col.add_child(head)
	var change := _change_view(p.get("before"), e)
	col.add_child(change)
	var heading: Label = change.get_child(0)  # "layer · name · kind" leads the header row
	change.remove_child(heading)
	heading.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	head.add_child(heading)
	head.add_child(_chip(Workbench.STATUS_NAMES.get(p.status, p.status),
		Workbench.STATUS_COLORS.get(p.status, UI.C_MUTED)))
	if p.get("reason") != null:
		col.add_child(_wrapped("为什么：" + str(p.reason), 15, UI.C_ME))
	if p.get("review_note") != null:
		var note := PanelContainer.new()
		note.add_theme_stylebox_override("panel", _note_style(Color("#fff3d6"), Color("#b07a12")))
		note.add_child(_wrapped("复核提示：" + str(p.review_note), 14))
		col.add_child(note)
	if not p.evidence.is_empty():
		var lines := PackedStringArray()
		for q in p.evidence.slice(0, 6):
			lines.append("「%s」" % str(q.quote))
		col.add_child(_wrapped("依据：" + "  ".join(lines), 14, UI.C_MUTED))
	var edit := TextEdit.new()
	edit.text = str(e.text)
	edit.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	edit.custom_minimum_size.y = 110
	edit.visible = false
	col.add_child(edit)
	var tried := _wrapped("", 14)
	tried.visible = false
	col.add_child(tried)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	col.add_child(row)
	var id := str(e.entry_id)
	row.add_child(_button("接受", func() -> void: _patch(id, {"status": "accepted"})))
	row.add_child(_button("拒绝", func() -> void: _patch(id, {"status": "rejected"})))
	var rewrite := _button("改写…", func() -> void: pass)
	rewrite.pressed.connect(func() -> void:
		if edit.visible:
			_patch(id, {"text": edit.text.strip_edges()})
		else:
			edit.visible = true
			rewrite.text = "保存改写")
	row.add_child(rewrite)
	var try_btn := _button("试答对比", func() -> void: pass)
	try_btn.tooltip_text = "让改前、改后的它回答同一个问题（约 30 秒）"
	try_btn.pressed.connect(func() -> void: _try(id, try_btn, tried))
	row.add_child(try_btn)
	return panel

func _patch(entry_id: String, body: Dictionary) -> void:
	var r := await _call("/v1/distillations/%s/entries/%s" % [_evo.distillation_id, entry_id.uri_encode()],
		HTTPClient.METHOD_PATCH, body)
	if not r.ok:
		return
	for i in _proposals.size():
		if str(_proposals[i].entry.entry_id) == entry_id:
			_proposals[i] = r.data
	_render_evolve()

func _try(entry_id: String, btn: Button, out: Label) -> void:
	btn.disabled = true
	btn.text = "试答中…"
	out.visible = true
	out.text = "两个版本正在回答同一个问题……"
	var r := await _call("/v1/distillations/%s/entries/%s:try" % [_evo.distillation_id, entry_id.uri_encode()],
		HTTPClient.METHOD_POST, {}, 180.0)
	btn.disabled = false
	btn.text = "再试一次"
	if not r.ok:
		out.text = "试答没有完成：" + str(r.error)
		return
	out.text = "问：%s\n改前的它：%s\n改后的它：%s" % [r.data.situation, r.data.before, r.data.after]

func _ask_commit() -> void:
	_confirm.title = "确认演化"
	_confirm.dialog_text = "接受的修改会成为 %s 的新版本。\n旧版本保留在演化历史里，随时可以回去。\n正在进行的聊天会先结束。" % app.persona_name
	_confirm_action = _commit
	app.popup_dialog(_confirm, Vector2i(480, 200), self)

func _commit() -> void:
	var r := await _call("/v1/distillations/%s:commit" % _evo.distillation_id, HTTPClient.METHOD_POST, {})
	if not r.ok:
		return
	_evo = {}
	_proposals = []
	_material = false
	app.persona_changed.emit("我好像有点不一样了。")
	await reload()
	_tabs.current_tab = TAB_HISTORY
	_say("演化完成，现在是 B%d。" % int(r.data.k))

func _ask_discard() -> void:
	_confirm.title = "放弃这次演化"
	_confirm.dialog_text = "这些修改建议都不会生效，人格保持原样。"
	_confirm_action = _discard
	app.popup_dialog(_confirm, Vector2i(440, 170), self)

func _discard() -> void:
	if _evo.is_empty():
		return
	var r := await _call("/v1/distillations/%s:discard" % _evo.distillation_id, HTTPClient.METHOD_POST, {})
	if r.ok:
		_evo = {}
		_proposals = []
		await _load_preview()

# ---- small builders -------------------------------------------------------------------------

func _card(parent: Control, heading: String) -> VBoxContainer:
	var panel := PanelContainer.new()
	panel.add_theme_stylebox_override("panel", _card_box(UI.C_SURFACE))
	parent.add_child(panel)
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 8)
	panel.add_child(box)
	if not heading.is_empty():
		box.add_child(UI.heading(heading, 18))
	return box

func _wrapped(text: String, font_size := 15, color := UI.C_TEXT) -> Label:
	var l := UI.label(text, font_size, color)
	l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	l.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	return l

func _chip(text: String, color: Color) -> Label:
	var l := UI.label(text, 13, color)
	var b := StyleBoxFlat.new()
	b.bg_color = UI.C_INPUT
	b.set_border_width_all(2)
	b.border_color = Color(color, 0.6)
	b.set_corner_radius_all(3)
	b.content_margin_left = 8
	b.content_margin_right = 8
	b.content_margin_top = 2
	b.content_margin_bottom = 2
	l.add_theme_stylebox_override("normal", b)
	l.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	return l

func _button(text: String, pressed: Callable) -> Button:
	var b := Button.new()
	b.text = text
	b.pressed.connect(func() -> void: app.note("人格窗口：" + text))
	b.pressed.connect(pressed)
	return b

func _flat(color: Color) -> StyleBoxFlat:
	var b := StyleBoxFlat.new()
	b.bg_color = color
	return b

func _card_box(color: Color) -> StyleBoxFlat:
	var b := StyleBoxFlat.new()
	b.bg_color = color
	b.set_border_width_all(2)
	b.border_color = Color(UI.C_FRAME, 0.6)
	b.set_corner_radius_all(4)
	b.set_content_margin_all(12)
	return b

func _note_style(bg: Color, edge: Color) -> StyleBoxFlat:
	var b := StyleBoxFlat.new()
	b.bg_color = bg
	b.border_width_left = 4
	b.border_color = edge
	b.set_content_margin_all(8)
	b.content_margin_left = 12
	return b

func _clear(box: Node) -> void:
	for c in box.get_children():
		box.remove_child(c)
		c.queue_free()
