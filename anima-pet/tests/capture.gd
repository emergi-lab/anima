extends SceneTree
## Saves screenshots of the pet (idle, walking, reacting), the avatar picker and the workbench.
## Needs a window (not --headless):
##   <Godot> --path . --script res://tests/capture.gd -- <out_dir>

var out := ""

func _initialize() -> void:
	_run.call_deferred()

func _shot(target: Viewport, name: String, frames := 20) -> void:
	for i in frames:
		await process_frame
	await RenderingServer.frame_post_draw
	target.get_texture().get_image().save_png(out.path_join(name + ".png"))
	print("saved ", name)

func _run() -> void:
	out = OS.get_cmdline_user_args()[0]
	DirAccess.make_dir_recursive_absolute(out)
	var app := root.get_node("App")
	app.use_settings("user://test.cfg")
	change_scene_to_file("res://scenes/pet.tscn")
	await _shot(root, "pet_idle", 30)
	var pet = current_scene
	pet.react("idea", 5.0)
	pet._walk()
	await _shot(root, "pet_walk_emote", 12)
	app.set_option("show_name", true)
	await _shot(root, "pet_name")
	app.set_option("show_name", false)
	var right := InputEventMouseButton.new()
	right.button_index = MOUSE_BUTTON_RIGHT
	right.pressed = true
	pet._unhandled_input(right)
	await _shot(pet._menu, "menu", 10)
	pet._menu.hide()
	app.open_picker()
	await _shot(app._picker, "picker", 30)
	app._picker.hide()
	# Workbench on whatever the backend (ANIMA_API) holds: the newest distillation, and in review
	# the first entry carrying a review note with its first evidence opened.
	app.open_workbench()
	var wb = app._workbench
	await create_timer(3.0).timeout
	await _shot(wb, "workbench")
	if str(wb._current.get("stage", "")) in ["review", "committed"]:
		for i in wb._entries.size():
			if wb._entries[i].review_note != null:
				wb._entry_index = i
				break
		wb._fill_tree()
		wb._show_entry()
		if wb._evidence.item_count > 0:
			await wb._on_evidence_selected(0)
		await _shot(wb, "workbench_entry")
	quit()
