extends SceneTree
## The chat window end to end against a running anima-server that holds the persona: expand
## from the input bar with half-typed text, send two lines, see the typing bubble and the reply,
## open its inner state, collapse back to the input bar and expand again. Needs a window:
##   ANIMA_API=… <Godot> --path . --script res://tests/chat_window_smoke.gd -- <out_dir> <persona_id> <name>

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
	print("chat window smoke: FAIL ", why)
	quit(1)

func _wait_reply(chat: PetChat) -> bool:
	var t := 0.0
	while chat.waiting and t < 90.0:
		await create_timer(0.5).timeout
		t += 0.5
	return not chat.waiting

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
	var chat: PetChat = pet._chat
	pet._on_menu(pet.Menu.TALK)
	chat.input._line.text = "八戒"
	await chat.expand(chat.input._line.text)
	var win := chat.window
	if not win.visible or win._box.text != "八戒":
		return _fail("expand lost the window or the text")
	await create_timer(1.0).timeout
	await _shot(win, "window_open")
	win._box.text = "八戒，你在干嘛呢？"
	win._submit()
	await _shot(win, "window_typing", 20)
	if not await _wait_reply(chat):
		return _fail("no answer in 90 s")
	print("bubble shown while windowed: ", chat.bubble.visible)
	await create_timer(0.3).timeout
	await _shot(win, "window_typing_out", 2)
	await create_timer(3.0).timeout
	await _shot(win, "window_reply")
	win._box.text = "那我们一起去吃点东西吧"
	win._submit()
	if not await _wait_reply(chat):
		return _fail("no second answer")
	await create_timer(7.0).timeout  # its state is stored a few seconds after the line shows
	var line: Control = win._timeline.last_line()
	await win._timeline.toggle_inner(line, line.get_meta("turn"))
	await _shot(win, "window_inner")
	win._box.text = "等一下"
	win.collapsed.emit(win._take_text())
	await create_timer(0.5).timeout
	if win.visible or not chat.input.visible or chat.input._line.text != "等一下":
		return _fail("collapse did not hand the text back to the input bar")
	await _shot(chat.input, "input_back")
	chat.input.expand_requested.emit("再来")
	await create_timer(1.5).timeout
	if not win.visible or win._box.text != "再来":
		return _fail("expand again failed")
	await _shot(win, "window_again")
	await chat.end_session()
	print("chat window smoke: PASS")
	quit()
