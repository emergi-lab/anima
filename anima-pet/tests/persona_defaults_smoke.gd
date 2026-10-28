extends SceneTree
## Offline regression: real App + windows, fake backend and a disposable settings file.
## Never renames/deletes a real persona or writes the owner's anima.cfg.

class FakeBackend extends Backend:
	var people: Array = []
	var replies: Array = []
	var calls: Array = []
	var history_delay := 0.0
	var history_result := {"ok": true, "data": []}
	func request_json(path: String, method := HTTPClient.METHOD_GET, _body: Variant = null,
			_timeout := 120.0) -> Dictionary:
		calls.append({"path": path, "method": method})
		if method == HTTPClient.METHOD_DELETE:
			var id := path.get_file()
			people = people.filter(func(p): return p.persona_id != id)
			return {"ok": true, "data": {"status": "deleted"}}
		if path == "/v1/personas":
			var cards := people.duplicate(true)
			for card in cards:
				card.merge({"k": 0, "last_chat_at": null}, false)
			var reply: Dictionary = replies.pop_front() if not replies.is_empty() else {
				"ok": true, "data": {"items": cards, "max": 5}}
			if float(reply.get("delay", 0)) > 0:
				await get_tree().create_timer(float(reply.delay)).timeout
			return reply
		if path.ends_with("/history"):
			var result := history_result.duplicate(true)
			if history_delay > 0:
				await get_tree().create_timer(history_delay).timeout
			return result
		if path.contains("/chats?") or path.contains("/memories?"):
			return {"ok": true, "data": []}
		if path == "/v1/settings/model":
			return {"ok": true, "data": {"ready": true}}
		return {"ok": false, "status": 404, "error": "unexpected fake request: " + path}

var failed := false

func _initialize() -> void:
	_run.call_deferred()

func _check(condition: bool, message: String) -> void:
	if not condition:
		failed = true
		push_error(message)

func _run() -> void:
	var app := root.get_node("App")
	app.set_process(false)
	var config := "user://persona-defaults-%d.cfg" % OS.get_process_id()
	app.use_settings(config)
	var api := FakeBackend.new()
	root.add_child(api)
	app.api = api
	_check(app.persona_id.is_empty() and app.persona_name == "anima", "Fresh name is anima")
	app._cfg.set_value("pet", "persona_name", "孙悟空")
	app._save()
	app._load_settings()
	_check(app.persona_name == "anima", "A legacy name without a persona ID loads as anima")
	app.set_persona("", "an old cached label")
	_check(app.persona_name == "anima", "An empty selection never keeps an old name")
	app.set_option("wander", false)
	app._cfg.set_value("looks", "other-persona", {"keep": "appearance"})
	app.set_persona("missing-persona", "孙悟空")
	api.replies = [{"ok": false, "error": "offline"}, {"ok": true, "data": {"items": [{}]}}]
	await app.refresh_personas()
	await app.refresh_personas()
	_check(app.persona_id == "missing-persona" and app.persona_name == "孙悟空",
		"Network failure / malformed roster must not erase a saved selection")
	await app.refresh_personas()
	_check(app.persona_id.is_empty() and app.persona_name == "anima", "Confirmed stale selection resets")
	var saved := ConfigFile.new()
	saved.load(config)
	_check(saved.get_value("pet", "persona_id") == "" and saved.get_value("pet", "persona_name") == "anima",
		"The repaired selection persists across launches")
	_check(not saved.get_value("pet", "wander") and saved.get_value("looks", "other-persona").keep == "appearance",
		"Repair preserves other settings and looks")
	_check(api.calls.all(func(c): return c.method == HTTPClient.METHOD_GET), "Repair is read-only on backend")
	api.people = [{"persona_id": "p-a", "name": "孙悟空"}]
	app.set_persona("p-a", "孙悟空")
	await app.refresh_personas()
	_check(app.persona_name == "孙悟空", "A real custom persona is not renamed")
	# A response started before the user switches must not clear the new selection.
	api.replies = [{"ok": true, "data": {"items": [], "max": 5}, "delay": 0.04}]
	app.refresh_personas()
	app.set_persona("p-b", "新人物")
	await create_timer(0.07).timeout
	_check(app.persona_id == "p-b" and app.persona_name == "新人物", "Protect a selection made during refresh")
	# An older, delayed empty roster must not replace a newer successful roster.
	api.people = [{"persona_id": "p-b", "name": "新人物"}]
	api.replies = [{"ok": true, "data": {"items": [], "max": 5}, "delay": 0.04}]
	app.refresh_personas()
	await app.refresh_personas()
	await create_timer(0.07).timeout
	_check(app.persona_id == "p-b" and app.personas.size() == 1, "Ignore out-of-order roster replies")
	# Explicit deletion still selects the next persona, then the neutral name.
	api.people = [{"persona_id": "p-a", "name": "甲"}, {"persona_id": "p-b", "name": "乙"}]
	app.set_persona("p-a", "甲")
	await app.delete_persona("p-a")
	_check(app.persona_id == "p-b" and app.persona_name == "乙", "Deletion still selects the next persona")
	await app.delete_persona("p-b")
	_check(app.persona_id.is_empty() and app.persona_name == "anima", "Deleting last persona restores anima")
	# Exercise the actual menu, input, persona window, and stale-window cleanup.
	var pet: Node2D = load("res://scenes/pet.tscn").instantiate()
	root.add_child(pet)
	pet.set_process(false)
	_check(pet._avatar.nickname == "anima", "Default name tag is anima")
	var talk_index: int = pet._menu.get_item_index(pet.Menu.TALK)
	_check(pet._menu.get_item_text(talk_index).contains("anima") and pet._menu.is_item_disabled(talk_index),
		"Menu uses anima without pretending a backend persona exists")
	pet._chat.input.open_for(app.persona_name)
	_check(pet._chat.input._line.placeholder_text.contains("anima"), "Default input hint uses anima")
	api.people = [{"persona_id": "p-a", "name": "孙悟空"}]
	app.set_persona("p-a", "孙悟空")
	await app.refresh_personas()
	_check(pet._menu.get_item_text(talk_index).contains("孙悟空"), "Menu keeps a valid persona's name")
	pet._chat.input.open_for(app.persona_name)
	pet._chat.window.show()
	var window := PersonaWindow.new()
	window.app = app
	root.add_child(window)
	app._persona = window
	window.show()
	window._version_label.text = "old version"
	window._layers["L3"].add_child(Label.new())
	var logs := ChatLog.new()
	logs.app = app
	root.add_child(logs)
	app._chat_log = logs
	logs.show()
	logs._memories.add_child(Label.new())
	api.people = []
	api.calls.clear()
	await window.reload()
	_check(window._title.text == "anima的人格" and window._status.text.contains("还没有人格"),
		"Missing cached persona produces a friendly anima empty state, not HTTP 404")
	_check(window._version_label.text.is_empty() and window._layers["L3"].get_child_count() == 0,
		"Stale persona content is cleared")
	_check(logs._title.text == "和anima的聊天" and logs._memories.get_child_count() == 1,
		"Chat log clears the old name and memories")
	_check(api.calls.all(func(c): return c.path == "/v1/personas"), "Never request the missing persona's history")
	_check(not pet._chat.input.visible and not pet._chat.window.visible, "Stale chat windows close")
	_check(pet._menu.is_item_disabled(talk_index) and pet._avatar.nickname == "anima", "Menu/name tag update together")
	# Opening chat history first also reconciles a stale saved selection.
	window.hide()
	logs.hide()
	app.set_persona("missing-persona", "孙悟空")
	logs.show()
	api.calls.clear()
	await logs.reload()
	_check(app.persona_name == "anima" and logs._title.text == "和anima的聊天",
		"Opening chat history resets a missing saved persona")
	_check(api.calls.all(func(c): return c.path == "/v1/personas"), "No chat requests for missing persona")
	window.show()
	# Defensive empty-history handling must not index into an empty array.
	api.people = [{"persona_id": "p-a", "name": "孙悟空"}]
	app.set_persona("p-a", "孙悟空")
	_check(window._status.text.contains("没有可显示的版本"), "Empty history is a safe empty state")
	app.set_persona("", "")
	# A previous persona request finishing late cannot repaint the corrected window.
	api.history_delay = 0.04
	api.history_result = {"ok": false, "status": 404, "error": "HTTP 404 old persona"}
	app.set_persona("p-a", "孙悟空")
	app.set_persona("", "")
	await create_timer(0.07).timeout
	_check(window._title.text == "anima的人格" and not window._status.text.contains("404"),
		"A late error cannot replace the empty state")
	app._persona = null
	app._chat_log = null
	window.queue_free()
	logs.queue_free()
	pet.queue_free()
	await process_frame
	DirAccess.remove_absolute(ProjectSettings.globalize_path(config))
	print("persona defaults smoke: ", "FAIL" if failed else "PASS")
	quit(1 if failed else 0)
