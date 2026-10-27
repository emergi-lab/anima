extends SceneTree
## Deterministic UI regression: delayed evidence, rapid selection, row updates and z-order.
## No live backend, model calls, or real settings writes. Run with a window.

class FakeBackend extends Backend:
	var reads := 0
	var rows: Array = []
	func request_json(path: String, method: int = HTTPClient.METHOD_GET,
			body: Variant = null, _timeout := 120.0) -> Dictionary:
		if "/evidence/" in path:
			reads += 1
			var id := path.get_file()
			await get_tree().create_timer(0.25 if id == "ev:0" else 0.02).timeout
			return {"ok": true, "data": {"source": "素材.txt", "construct": "value",
				"note": "证据 " + id, "quote": id, "quote_offset": 0,
				"segment_text": id + "\n" + "这是一段用于原文排版的中文素材。".repeat(250)}}
		if method == HTTPClient.METHOD_PATCH:
			await get_tree().create_timer(0.1).timeout
			for row in rows:
				if row.entry.entry_id == path.get_file():
					var changed: Dictionary = row.duplicate(true)
					changed.status = body.status
					return {"ok": true, "data": changed}
		return {"ok": true, "data": {"ready": true, "source": "env", "backend": "fake",
			"base_url": "http://127.0.0.1:9/v1", "model": "offline-test", "key_set": false,
			"key_hint": "", "disable_thinking": false}}

class ProbeWorkbench extends Workbench:
	func _refresh_list(_select_id := "") -> void:
		pass

func _initialize() -> void:
	_run.call_deferred()

func _select(wb: Workbench, index: int) -> void:
	wb._entry_items["entry-%d" % index].select(0)
	wb._on_tree_selected()

func _run() -> void:
	var app := root.get_node("App")
	var config := "user://review-test-%d.cfg" % OS.get_process_id()
	app.use_settings(config)
	var api := FakeBackend.new()
	root.add_child(api)
	app.api = api
	var wb := ProbeWorkbench.new()
	wb.app = app
	wb.theme = app.theme
	root.add_child(wb)
	wb.set_process(false)
	wb._current = {"distillation_id": "test", "stage": "review"}
	for i in 63:
		wb._entries.append({"status": "proposed", "review_note": "依据具体情境审阅。" if i % 2 else null,
			"original_text": null, "entry": {"entry_id": "entry-%d" % i, "layer": "L3",
				"construct": "overall_identity", "construct_id": "overall_identity.self_view",
				"text": "我会根据经历和具体情境形成自己的看法。".repeat(6),
				"scope": [], "source": "inference", "value": {"baseline": 0.7}, "confidence": 0.8},
			"evidence": [{"evidence_id": "ev:%d" % i, "quote": "中文证据 %d" % i}]})
	api.rows = wb._entries
	wb._setup.hide()
	wb._progress.hide()
	wb._done.hide()
	wb._review.show()
	wb._fill_tree()
	app.show_window(wb, wb.BASE_SIZE, wb.MIN_SIZE)
	await create_timer(0.3).timeout
	var times: Array[float] = []
	var frames: Array[float] = []
	for i in 30:
		var start := Time.get_ticks_usec()
		_select(wb, i)
		times.append((Time.get_ticks_usec() - start) / 1000.0)
		await RenderingServer.frame_post_draw
		frames.append((Time.get_ticks_usec() - start) / 1000.0)
		await process_frame
	times.sort()
	frames.sort()
	print("63-entry review: selection_ms p50=", times[times.size()/2], " max=", times[-1],
		" frame_ms p50=", frames[frames.size()/2], " max=", frames[-1])
	_select(wb, 0)
	wb._on_evidence_selected(0) # deliberately slow
	assert(wb._context.text.contains("正在读取"))
	_select(wb, 1)
	await wb._on_evidence_selected(0) # fast response must remain visible
	await create_timer(0.3).timeout
	assert(wb._context.text.contains("ev:1") and not wb._context.text.contains("ev:0"))
	var reads := api.reads
	_select(wb, 0)
	var start := Time.get_ticks_usec()
	await wb._on_evidence_selected(0)
	print("cached evidence display_ms=", (Time.get_ticks_usec() - start) / 1000.0)
	assert(api.reads == reads and wb._context.text.contains("ev:0"))
	# Clicking the same row must preserve an unsaved edit and the selected evidence.
	wb._text.text = "尚未保存的改写"
	wb._on_tree_selected()
	assert(wb._text.text == "尚未保存的改写" and wb._context.text.contains("ev:0"))
	# A delayed PATCH belongs to its original row, even after selection changes.
	var tree_root := wb._tree.get_root()
	var row_item: TreeItem = wb._entry_items["entry-0"]
	wb._patch({"status": "accepted"})
	_select(wb, 1)
	await create_timer(0.2).timeout
	assert(wb._entries[0].status == "accepted" and wb._entries[1].status == "proposed")
	assert(wb._tree.get_root() == tree_root and wb._entry_items["entry-0"] == row_item)
	assert(wb._entry_index == 1 and row_item.get_text(0).contains("通过"))
	# Switching entries before an uncached response returns must leave the details blank.
	_select(wb, 2)
	wb._on_evidence_selected(0)
	_select(wb, 3)
	await create_timer(0.1).timeout
	assert(wb._context.text.is_empty())
	_select(wb, 4)
	wb._on_evidence_selected(0)
	wb._current.distillation_id = "another-project"
	wb._show_entry()
	await create_timer(0.1).timeout
	assert(wb._context.text.is_empty())
	wb._current.distillation_id = "test"
	assert(not root.always_on_top and not wb.always_on_top)
	app.open_settings()
	assert(not app._settings.always_on_top)
	app._settings.hide()
	var input := ChatInput.new()
	input.app = app
	input.theme = app.theme
	root.add_child(input)
	input.open_for("测试")
	assert(not input.always_on_top)
	input.hide()
	var bubble := ChatBubble.new()
	bubble.app = app
	bubble.theme = app.theme
	root.add_child(bubble)
	bubble.say("窗口层级检查")
	assert(not bubble.always_on_top)
	bubble.hide()
	_select(wb, 1)
	await wb._on_evidence_selected(0)
	await create_timer(0.1).timeout
	await RenderingServer.frame_post_draw
	wb.get_texture().get_image().save_png("user://review-interaction.png")
	DirAccess.remove_absolute(ProjectSettings.globalize_path(config))
	print("review interaction: PASS (cache, stale responses, edit preservation, incremental update, ordinary windows)")
	quit()
