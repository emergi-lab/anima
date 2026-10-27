extends SceneTree
## Chat end to end against a running anima-server that already holds the persona:
## open the chat, say a line, wait for the answer in the bubble, open the log and an explanation,
## end the session. Saves screenshots of each window. Needs a window (not --headless):
##   ANIMA_API=… <Godot> --path . --script res://tests/chat_smoke.gd -- <out_dir> <persona_id> <name> [line]

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
	print("chat smoke: FAIL ", why)
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
	var chat: PetChat = pet._chat
	pet._on_menu(pet.Menu.TALK)
	await _shot(chat.input, "chat_input")
	chat.input._line.text = args[3] if args.size() > 3 else "你还记得我们刚才聊了什么吗？"
	chat.input._submit()
	await _shot(chat.bubble, "bubble_thinking", 20)
	await _shot(pet.get_window(), "pet_thinking", 2)
	var t := 0.0
	while chat.waiting and t < 90.0:
		await create_timer(0.5).timeout
		t += 0.5
	if chat.waiting:
		return _fail("no answer in 90 s")
	await create_timer(1.2).timeout
	await _shot(chat.bubble, "bubble_typing", 2)
	await _shot(pet.get_window(), "pet_talking", 2)
	chat.bubble._page_typed()
	await _shot(chat.bubble, "bubble_page", 4)
	print("bubble pages: ", chat.bubble._pages)

	app.open_chat_log()
	var log: ChatLog = app._chat_log
	await create_timer(2.0).timeout
	await _shot(log, "log", 10)
	await create_timer(6.0).timeout  # the newest line is stored a few seconds after it shows
	await log.reload()
	await create_timer(1.0).timeout
	var turn: Dictionary = log._data[0].turns[-1]  # the newest line: the persona's answer
	var line: Control = log._timeline.last_line()
	if turn.speaker != "persona":
		return _fail("the newest line is not the persona's")
	await log._timeline.toggle_inner(line, turn)
	await _shot(log, "log_inner", 10)
	await chat.end_session()
	for i in 60:  # the memory job runs in the background after the session ends
		var m: Dictionary = await app.api.request_json("/v1/personas/%s/memories" % args[1])
		if m.ok and not m.data.is_empty():
			break
		await create_timer(1.0).timeout
	await log.reload()
	log._tabs.current_tab = 1
	await create_timer(1.0).timeout
	await _shot(log, "log_memories", 10)
	print("chat smoke: PASS")
	quit()
