extends SceneTree
## Reflection end to end against a running anima-server that holds the persona and some chats:
## start a reflection in the persona window, wait for the pet to announce it, look at the diary
## and each version, try one, approve it, and see it in the history. It really commits a new
## version, so run it on a copy of the database. Needs a window:
##   ANIMA_API=… <Godot> --path . --script res://tests/reflection_smoke.gd -- <out_dir> <persona_id> <name>

var out := ""

func _initialize() -> void:
	_run.call_deferred()

func _shot(target: Viewport, name: String, frames := 12) -> void:
	for i in frames:
		await process_frame
	await RenderingServer.frame_post_draw
	target.get_texture().get_image().save_png(out.path_join(name + ".png"))
	print("saved ", name)

func _fail(why: String) -> void:
	print("reflection smoke: FAIL ", why)
	quit(1)

func _run() -> void:
	var args := OS.get_cmdline_user_args()
	out = args[0]
	DirAccess.make_dir_recursive_absolute(out)
	var app := root.get_node("App")
	app.use_settings("user://test.cfg")
	app.set_persona(args[1], args[2])
	change_scene_to_file("res://scenes/pet.tscn")
	for i in 5:
		await process_frame
	var pet = current_scene
	pet._on_menu(pet.Menu.PERSONA)
	var win: PersonaWindow = app._persona
	await create_timer(2.0).timeout
	win._tabs.current_tab = PersonaWindow.TAB_EVOLVE
	if not win._refl.is_empty():  # one left over: decline it and start afresh
		await win._decline_reflection()
		await create_timer(1.0).timeout
	await _shot(win, "reflect_start")
	if win._refl_preview.is_empty() or not win._refl_preview.enough:
		return _fail("nothing to reflect on: " + str(win._refl_preview))
	await win._start_reflection()
	await _shot(win, "reflect_running")
	var t := 0.0
	while str(win._refl.get("status", "")) == "running" and t < 120.0:
		await create_timer(1.0).timeout
		t += 1.0
	await create_timer(1.0).timeout
	print("status: ", win._refl.get("status"), " bubble: ", pet._chat.bubble.visible, " versions: ",
		win._refl.get("versions", []).map(func(v): return "%s(%d)" % [v.key, v.changes.size()]))
	await _shot(pet._chat.bubble, "pet_announce", 2)
	if win._refl.get("status") != "ready" or win._refl.versions.is_empty():
		return _fail("no versions: " + str(win._refl))
	win._tabs.current_tab = PersonaWindow.TAB_EVOLVE
	for v in win._refl.versions:
		win._version_key = str(v.key)
		win._render_evolve()
		await _shot(win, "reflect_version_" + str(v.key))
	var key := "B" if win._refl.versions.any(func(v): return v.key == "B") else str(win._refl.versions[0].key)
	var label := Label.new()
	var btn := Button.new()
	await win._try_version(key, btn, label)
	print("try: ", label.text)
	await win._approve(key)
	await create_timer(1.5).timeout
	win._tabs.current_tab = PersonaWindow.TAB_HISTORY
	await _shot(win, "history")
	print("reflection smoke: PASS ", win._version_label.text)
	quit()
