extends SceneTree
## Native desktop screenshots of the settings toggle, floor placement and long names.
## No model calls or real settings writes. Run without --headless.

func _initialize() -> void:
	_run.call_deferred()

func _shot(name: String) -> void:
	await create_timer(0.3).timeout
	await RenderingServer.frame_post_draw
	var screen := root.current_screen
	var shot := DisplayServer.screen_get_image(screen)
	var origin := root.position - DisplayServer.screen_get_position(screen)
	shot.get_region(Rect2i(origin, root.size)).save_png("user://name-tag-%s.png" % name)

func _run() -> void:
	var app := root.get_node("App")
	var config := "user://name-tag-test-%d.cfg" % OS.get_process_id()
	app.use_settings(config)
	app.api.base_url = "http://127.0.0.1:9"
	app.persona_name = "名牌测试"
	app.wander = false
	change_scene_to_file("res://scenes/pet.tscn")
	await scene_changed
	var pet := current_scene
	pet.set_process(false)
	app.open_settings()
	app._settings.hide()
	assert(not pet._avatar.show_name)
	await _shot("off")
	app._settings._name_tag.button_pressed = true
	assert(app.show_name and pet._avatar.show_name)
	assert(root.position.y + root.size.y <= pet._usable().end.y)
	var saved := ConfigFile.new()
	assert(saved.load(config) == OK and saved.get_value("pet", "show_name"))
	await _shot("on")
	app.persona_name = "这是一个特别长的桌宠名字"
	app.look_changed.emit()
	app.set_option("zoom", 1.75)
	assert(root.position.y + root.size.y <= pet._usable().end.y)
	await _shot("long-large")
	app._settings._name_tag.button_pressed = false
	assert(not app.show_name and not pet._avatar.show_name)
	await _shot("off-again")
	DirAccess.remove_absolute(ProjectSettings.globalize_path(config))
	print("name tag: PASS (toggle, saved setting, floor placement, long name, large scale)")
	quit()
