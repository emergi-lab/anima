extends SceneTree
## Exercises the real App.delete_persona path against an in-memory backend only.

class FakeBackend extends Backend:
	var people := [{"persona_id": "p-a", "name": "测试甲"}, {"persona_id": "p-b", "name": "测试乙"}]
	var deleted: Array[String] = []
	var busy := false
	var old_server := false
	func request_json(path: String, method := HTTPClient.METHOD_GET, _body: Variant = null,
			_timeout := 120.0) -> Dictionary:
		if method == HTTPClient.METHOD_DELETE:
			var all_data := path.ends_with("/all-data")
			var id := path.trim_suffix("/all-data").get_file()
			deleted.append(id)
			await get_tree().create_timer(0.05).timeout
			if busy:
				return {"ok": false, "code": "persona_busy", "error": "busy"}
			if all_data and old_server:
				return {"ok": false, "status": 404, "error": "old backend"}
			people = people.filter(func(p): return p.persona_id != id)
			return {"ok": true, "data": {"status": "deleted_all" if all_data else "deleted"}}
		if path == "/v1/personas":
			return {"ok": true, "data": {"items": people.duplicate(true), "max": 5}}
		return {"ok": true, "data": {"ready": true}}

class ProbeWorkbench extends Workbench:
	func _refresh_list(_id := "") -> void:
		pass
	func _load_done() -> void:
		pass
	func _load(id: String) -> void:
		_current = _list.filter(func(d): return d.distillation_id == id)[0]
		_render()

func _initialize() -> void:
	_run.call_deferred()

func _project(id: String, display_name: String) -> Dictionary:
	return {"distillation_id": "d-" + id, "persona_id": id, "target": {"name": display_name},
		"stage": "committed", "sources": [], "created_at": "2026-10-27T00:00:00Z",
		"progress": {"relevant": 0, "extracted": 0, "failed": 0, "evidence": 0, "entries": 0},
		"usage": {"model_calls": 0, "prompt_tokens": 0, "completion_tokens": 0}, "error": null}

func _run() -> void:
	var app := root.get_node("App")
	var config := "user://delete-test-%d.cfg" % OS.get_process_id()
	app.use_settings(config)
	var api := FakeBackend.new()
	root.add_child(api)
	app.api = api
	await app.refresh_personas()
	app.set_persona("p-a", "测试甲")
	var wb := ProbeWorkbench.new()
	wb.app = app
	wb.theme = app.theme
	root.add_child(wb)
	wb._list = [_project("p-a", "测试甲"), _project("p-b", "测试乙")]
	wb._current = wb._list[0]
	wb._reviewing = true
	wb._render()
	app.show_window(wb, wb.BASE_SIZE, wb.MIN_SIZE)
	await create_timer(0.1).timeout
	assert(wb._delete_persona_button.visible)
	wb._delete_persona_button.pressed.emit()
	assert(wb._delete_persona_dialog.visible and api.deleted.is_empty())
	assert(wb._delete_description.text.contains("蒸馏素材和审阅结果会保留"))
	assert(not wb._delete_all_data.button_pressed)
	wb._delete_all_data.button_pressed = true
	assert(wb._delete_description.text.contains("所有关联蒸馏项目"))
	assert(wb._delete_persona_dialog.ok_button_text == "永久删除全部信息")
	wb._delete_all_data.button_pressed = false
	await process_frame
	await RenderingServer.frame_post_draw
	wb._delete_persona_dialog.get_texture().get_image().save_png("user://delete-confirm-preview.png")
	wb._delete_persona_dialog.get_cancel_button().pressed.emit()
	assert(wb._pending_persona.is_empty() and api.deleted.is_empty())
	# Confirmation is bound to A even if the current project changes before submission.
	wb._ask_delete_persona()
	wb._current = wb._list[1]
	wb._render()
	wb._delete_persona_dialog.confirmed.emit()
	wb._delete_persona_dialog.hide()
	wb._delete_persona() # duplicate activation must not send another request
	assert(wb._delete_persona_button.disabled)
	await create_timer(0.15).timeout
	assert(api.deleted == ["p-a"] and app.persona_id == "p-b")
	assert(wb._current.persona_id == "p-b" and wb._delete_persona_button.visible)
	# A busy failure keeps the persona and lets the owner retry.
	api.busy = true
	wb._ask_delete_persona()
	wb._delete_persona_dialog.confirmed.emit()
	wb._delete_persona_dialog.hide()
	await create_timer(0.15).timeout
	assert(app.personas.size() == 1 and not wb._delete_persona_button.disabled)
	assert(wb._status.text.contains("正在蒸馏或演化"))
	api.busy = false
	wb._ask_delete_persona()
	wb._delete_persona_dialog.confirmed.emit()
	wb._delete_persona_dialog.hide()
	await create_timer(0.15).timeout
	assert(app.personas.is_empty() and app.persona_id.is_empty())
	assert(wb._delete_persona_button.text == "删除全部信息…" and wb._deleted and wb._review.visible)
	assert(wb._list.size() == 2 and wb._current.stage == "committed")
	assert(wb._done_text.text.contains("之前的聊天和记忆不会回来"))
	# Already-deleted personas can purge their retained projects. An old backend is an error.
	wb._ask_delete_persona()
	assert(wb._delete_all_data.button_pressed and wb._delete_all_data.disabled)
	assert(wb._delete_description.text.contains("所有关联蒸馏项目"))
	api.old_server = true
	wb._delete_persona_dialog.confirmed.emit()
	wb._delete_persona_dialog.hide()
	await create_timer(0.15).timeout
	assert(wb._list.size() == 2 and not wb._delete_persona_button.disabled)
	api.old_server = false
	wb._ask_delete_persona()
	await process_frame
	await RenderingServer.frame_post_draw
	wb._delete_persona_dialog.get_texture().get_image().save_png("user://delete-all-confirm-preview.png")
	wb._delete_persona_dialog.confirmed.emit()
	wb._delete_persona_dialog.hide()
	await create_timer(0.15).timeout
	assert(wb._list.size() == 1 and wb._current.persona_id == "p-a")
	wb._ask_delete_persona()
	wb._delete_persona_dialog.confirmed.emit()
	wb._delete_persona_dialog.hide()
	await create_timer(0.15).timeout
	assert(wb._list.is_empty() and wb._current.is_empty() and wb._entries.is_empty())
	assert(wb._sidebar.get_child_count() == 0 and not wb._delete_persona_button.visible)
	DirAccess.remove_absolute(ProjectSettings.globalize_path(config))
	print("workbench delete: PASS (scope toggle, cancel, target binding, duplicate guard, busy retry, roster, full cleanup, old backend)")
	quit()
