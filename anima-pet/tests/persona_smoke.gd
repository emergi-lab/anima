extends SceneTree
## Persona window end to end against a running anima-server that holds the persona and some
## chats: show the three layers and the history, run an evolution, accept its proposals, try one,
## commit it. Saves screenshots of each step. Needs a window (not --headless):
##   ANIMA_API=… <Godot> --path . --script res://tests/persona_smoke.gd -- <out_dir> <persona_id> <name>

var out := ""

func _initialize() -> void:
	_run.call_deferred()

func _shot(target: Viewport, name: String, frames := 10) -> void:
	for i in frames:
		await process_frame
	await RenderingServer.frame_post_draw
	target.get_texture().get_image().save_png(out.path_join(name + ".png"))
	print("saved ", name)

func _fail(why: String) -> void:
	print("persona smoke: FAIL ", why)
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
	if win._version.is_empty():
		return _fail("no version loaded: " + win._status.text)
	for i in 3:
		win._tabs.current_tab = i
		await _shot(win, "layer_%d" % i)
	# the evidence of the first card
	var first: Control = win._layers["L3"].get_child(1)
	for c in first.get_child(0).get_children():
		if c is Button:
			c.pressed.emit()
	win._tabs.current_tab = 0
	await create_timer(1.0).timeout
	await _shot(win, "layer_0_quotes")

	win._tabs.current_tab = PersonaWindow.TAB_EVOLVE
	await _shot(win, "evolve_start")
	print("preview: ", win._preview)
	if win._evo.is_empty() or win._evo.stage == "draft":
		await win._start_evolution()
	var t := 0.0
	while win._evo.get("stage", "") == "running" and t < 300.0:
		if t == 2.0:
			await _shot(win, "evolve_running", 2)
		await create_timer(1.0).timeout
		t += 1.0
	await create_timer(1.0).timeout
	win._tabs.current_tab = PersonaWindow.TAB_EVOLVE
	print("evolution: ", win._evo.stage, " entries ", win._proposals.size(), " usage ", win._evo.usage)
	await _shot(win, "evolve_review")
	await _shot(pet.get_window(), "pet_announce", 2)
	await _shot(pet._chat.bubble, "bubble_announce", 2)
	if win._evo.stage != "review":
		return _fail("evolution ended in " + str(win._evo.stage) + " " + str(win._evo.get("error")))
	for p in win._proposals:
		print("- ", p.entry.construct_id, " | ", p.get("reason"), "\n  before: ",
			p.before.text if p.get("before") != null else "(new)", "\n  after:  ", p.entry.text)
	if win._proposals.is_empty():
		print("persona smoke: PASS (nothing to change)")
		return quit()
	for p in win._proposals:
		await win._patch(str(p.entry.entry_id), {"status": "accepted"})
	# try the first proposal before vs after
	var card: Control = win._evolve_box.get_child(1)
	var col: VBoxContainer = card.get_child(0)
	var row: HBoxContainer = col.get_child(col.get_child_count() - 1)
	var try_btn: Button = row.get_child(3)
	var tried: Label = col.get_child(col.get_child_count() - 2)
	await win._try(str(win._proposals[0].entry.entry_id), try_btn, tried)
	print("try: ", tried.text)
	win._tabs.current_tab = PersonaWindow.TAB_EVOLVE
	await _shot(win, "evolve_tried")
	await win._commit()
	await create_timer(1.0).timeout
	win._tabs.current_tab = PersonaWindow.TAB_HISTORY
	await _shot(win, "history")
	await _shot(pet._chat.bubble, "bubble_changed", 2)
	win._tabs.current_tab = 0
	await _shot(win, "layer_0_after")
	print("persona smoke: PASS ", win._version_label.text)
	quit()
