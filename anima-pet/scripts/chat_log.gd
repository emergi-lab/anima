class_name ChatLog
extends Window
## Chat log: every line with the persona as one timeline (ChatTimeline), newest at the bottom,
## with a line per day and the start time of each chat; chips above show one day at a time.
## A second tab lists what it remembers from earlier chats.

const BASE_SIZE := Vector2i(1000, 760)
const MIN_SIZE := Vector2i(820, 560)
const PAGE := 30  # chats per load; "加载更早的消息" asks for more

## The App autoload, injected on creation (a global name would not resolve in test scripts).
var app: Node
var _title: Label
var _status: Label
var _tabs: TabContainer
var _days: HFlowContainer
var _timeline: ChatTimeline
var _memories: VBoxContainer
var _memory_empty: Label
var _data: Array = []  # chats from the server, newest first
var _limit := PAGE
var _day := ""  # "" = every day, else a local "YYYY-MM-DD"

func _init() -> void:
	title = "Anima · 聊天记录"
	size = BASE_SIZE
	min_size = MIN_SIZE
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
		margin.add_theme_constant_override("margin_" + side, 16)
	add_child(margin)
	var root := VBoxContainer.new()
	root.add_theme_constant_override("separation", 10)
	margin.add_child(root)

	var head := HBoxContainer.new()
	head.add_theme_constant_override("separation", 12)
	root.add_child(head)
	_title = UI.pixel_label("聊天记录", 24)
	head.add_child(_title)
	_status = UI.label("", 13, UI.C_MUTED)
	_status.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_status.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	head.add_child(_status)
	var refresh := Button.new()
	refresh.text = "刷新"
	refresh.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	refresh.pressed.connect(reload)
	head.add_child(refresh)

	_tabs = TabContainer.new()
	_tabs.size_flags_vertical = Control.SIZE_EXPAND_FILL
	root.add_child(_tabs)
	_tabs.add_child(_build_chats())
	_tabs.add_child(_build_memories())
	_tabs.set_tab_title(0, "对话")
	_tabs.set_tab_title(1, "它记得的事")

func _build_chats() -> Control:
	var col := VBoxContainer.new()
	col.add_theme_constant_override("separation", 8)
	_days = HFlowContainer.new()
	_days.add_theme_constant_override("h_separation", 6)
	_days.add_theme_constant_override("v_separation", 6)
	col.add_child(_days)
	var paper := PanelContainer.new()
	paper.size_flags_vertical = Control.SIZE_EXPAND_FILL
	col.add_child(paper)
	_timeline = ChatTimeline.new()
	_timeline.app = app
	_timeline.more_requested.connect(func() -> void:
		_limit += PAGE
		reload())
	paper.add_child(_timeline)
	return col

func _build_memories() -> Control:
	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	_memories = VBoxContainer.new()
	_memories.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_memories.add_theme_constant_override("separation", 8)
	scroll.add_child(_memories)
	_memory_empty = UI.label("还没有记忆。一段对话结束（闲置 10 分钟、或退出桌宠）后，它会把聊过的事整理成记忆。",
		15, UI.C_MUTED)
	_memory_empty.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_memories.add_child(_memory_empty)
	return scroll

# ---- data -----------------------------------------------------------------------------------

func reload() -> void:
	if app.persona_id.is_empty():
		_status.text = "还没有人格。先在蒸馏工作台导入一个。"
		_data = []
		_fill()
		return
	_title.text = "和%s的聊天" % app.persona_name
	_status.text = "读取中…"
	var r: Dictionary = await app.api.request_json("/v1/personas/%s/chats?limit=%d" % [
		app.persona_id.uri_encode(), _limit])
	if not r.ok:
		_status.text = str(r.error)
		return
	_data = r.data
	var lines := 0
	for sess in _data:
		lines += sess.turns.size()
	_status.text = "%d 段聊天 · %d 句" % [_data.size(), lines] if not _data.is_empty() \
		else "还没聊过。右键桌宠选\"对话\"，或者双击它。"
	_fill()
	_load_memories()

func _fill() -> void:
	_fill_days()
	_fill_timeline()

static func _date(iso: Variant) -> String:
	return UI.local_time(iso).left(10)

static func _clock(iso: Variant) -> String:
	return UI.local_time(iso).substr(11, 5)

## "今天" / "昨天" / "10月21日" for a local date.
static func _day_name(date: String) -> String:
	var today := Time.get_date_string_from_system()
	var yesterday := Time.get_date_string_from_unix_time(int(Time.get_unix_time_from_system()) - 86400
		+ int(Time.get_time_zone_from_system().get("bias", 0)) * 60)
	if date == today:
		return "今天"
	if date == yesterday:
		return "昨天"
	return "%d月%d日" % [int(date.substr(5, 2)), int(date.substr(8, 2))]

func _fill_days() -> void:
	for c in _days.get_children():
		_days.remove_child(c)
		c.queue_free()
	var dates: Array = []
	for sess in _data:  # newest first
		var d := _date(sess.started_at)
		if not dates.has(d):
			dates.append(d)
	if not dates.has(_day):
		_day = ""
	_days.add_child(_chip("全部", ""))
	for d in dates:
		_days.add_child(_chip(_day_name(d), d))

func _chip(text: String, day: String) -> Button:
	var b := Button.new()
	b.text = text
	b.toggle_mode = true
	b.button_pressed = day == _day
	b.pressed.connect(func() -> void:
		_day = day
		_fill())
	return b

func _fill_timeline() -> void:
	_timeline.clear(_data.size() >= _limit and _day.is_empty())
	var last_date := ""
	var any := false
	for i in range(_data.size() - 1, -1, -1):  # oldest first
		var sess: Dictionary = _data[i]
		var date := _date(sess.started_at)
		if not _day.is_empty() and date != _day:
			continue
		if date != last_date:
			_timeline.add_divider("———  %s  ———" % _day_name(date), 15)
			last_date = date
		_timeline.add_divider(_clock(sess.started_at))
		for t in sess.turns:
			_timeline.add_turn(t)
			any = true
	if not any:
		_timeline.add_divider("这一天没有聊天。" if not _day.is_empty() else "还没聊过。", 15)
	_timeline.scroll_to_end()

# ---- memories -------------------------------------------------------------------------------

func _load_memories() -> void:
	var r: Dictionary = await app.api.request_json("/v1/personas/%s/memories?limit=50" % app.persona_id.uri_encode())
	for c in _memories.get_children():
		if c != _memory_empty:
			c.queue_free()
	if not r.ok:
		_memory_empty.text = str(r.error)
		_memory_empty.visible = true
		return
	_memory_empty.visible = r.data.is_empty()
	for m in r.data:
		var card := PanelContainer.new()
		card.add_theme_stylebox_override("panel", ChatTimeline.bubble_box(false))
		var col := VBoxContainer.new()
		card.add_child(col)
		var about := "关于%s · " % str(m.about_name) if m.get("about_name") != null else ""
		col.add_child(UI.label("%s%s" % [about, UI.local_time(m.created_at)], 12, UI.C_MUTED))
		var text := UI.label(str(m.text), 15)
		text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		text.custom_minimum_size.x = 600
		col.add_child(text)
		_memories.add_child(card)
