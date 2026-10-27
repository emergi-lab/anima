extends RefCounted
## Run through Godot MCP game_eval on an open review page:
## return await load('res://tests/review_latency_probe.gd').new().run(get_node('/root/App')._workbench)
## Read-only: routes mouse input through the actual Tree and waits for rendered frames.
## Does not submit reviews, call models, or save configuration.

func run(wb: Workbench) -> Dictionary:
	if not wb.visible or not wb._review.visible or wb._entries.is_empty():
		return {"error": "Open a populated review page first."}
	var selected := wb._entry_index
	var input_ms: Array[float] = []
	var draw_ms: Array[float] = []
	var mismatches: Array[int] = []
	for i in wb._entries.size():
		var item: TreeItem = wb._entry_items[str(wb._entries[i].entry.entry_id)]
		wb._tree.scroll_to_item(item, true)
		await wb.get_tree().process_frame
		# Container/Tree scrolling is finalized during the draw phase. At higher FPS,
		# sampling before it finishes can target the previous row's coordinates.
		await RenderingServer.frame_post_draw
		var area := wb._tree.get_item_area_rect(item)
		var event := InputEventMouseButton.new()
		event.button_index = MOUSE_BUTTON_LEFT
		event.position = wb._tree.global_position + area.position + Vector2(80, area.size.y / 2)
		event.pressed = true
		var start := Time.get_ticks_usec()
		wb.push_input(event, true)
		input_ms.append((Time.get_ticks_usec() - start) / 1000.0)
		event.pressed = false
		wb.push_input(event, true)
		await RenderingServer.frame_post_draw
		draw_ms.append((Time.get_ticks_usec() - start) / 1000.0)
		if wb._entry_index != i:
			mismatches.append(i)
	if selected >= 0:
		var item: TreeItem = wb._entry_items[str(wb._entries[selected].entry.entry_id)]
		item.select(0)
		wb._on_tree_selected()
		wb._tree.scroll_to_item(item)
	input_ms.sort()
	draw_ms.sort()
	return {"entries": input_ms.size(), "selection_mismatches": mismatches,
		"input_ms": _summary(input_ms), "draw_ms": _summary(draw_ms),
		"fps": Engine.get_frames_per_second()}

func _summary(samples: Array[float]) -> Dictionary:
	return {"p50": samples[samples.size() / 2],
		"p95": samples[mini(samples.size() - 1, int(samples.size() * 0.95))],
		"max": samples[-1]}
