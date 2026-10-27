extends SceneTree
## No API key yet, then one: the reminders (head bubble at start, a chat line, the workbench
## banner, the button dialog) and the way back once the key is saved on the settings page.
## Needs a window and an anima-server without a key that holds the persona:
##   ANIMA_API=… ANIMA_PROBE_KEY=… <Godot> --path . --script res://tests/model_setup_smoke.gd -- <out_dir> <persona_id> <name>

var out := ""

func _shot(target: Viewport, name: String) -> void:
	for i in 10:
		await process_frame
	await RenderingServer.frame_post_draw
	target.get_texture().get_image().save_png(out.path_join(name + ".png"))

func _check(ok: bool, what: String) -> void:
	print(("ok   " if ok else "FAIL ") + what)
	if not ok:
		quit(1)

func _initialize() -> void:
	_run.call_deferred()

func _run() -> void:
	var args := OS.get_cmdline_user_args()
	out = args[0]
	DirAccess.make_dir_recursive_absolute(out)
	var app := root.get_node("App")
	app.use_settings("user://test.cfg")
	app.set_persona(args[1], args[2])
	change_scene_to_file("res://scenes/pet.tscn")
	await create_timer(3.0).timeout
	var chat: PetChat = null
	for n in current_scene.find_children("*", "", true, false):
		if n is PetChat:
			chat = n
	_check(not app.model_ready, "server without a key is seen as not ready")
	_check(chat.bubble.visible and chat.bubble._pages.size() == 1
		and chat.bubble._pages[0].contains("模型 API"),
		"start: head bubble says to fill in the API, on one page — " + " / ".join(chat.bubble._pages))
	await _shot(chat.bubble, "1_start_bubble")
	chat.bubble.dismiss()
	chat.bubble._action = Callable()

	chat.open()
	chat.input._line.text = "你好呀"
	chat.input._submit()
	await create_timer(1.0).timeout
	_check(chat.bubble._action.is_valid(), "chat line answers with a link — " + " / ".join(chat.bubble._pages))
	_check(chat.input._line.text == "你好呀", "the unsent line is back in the input bar")
	await _shot(chat.bubble, "2_chat_reply")

	await chat.expand("再试一次")
	chat.window._box.text = "再试一次"
	chat.window._send.pressed.emit()
	await create_timer(1.0).timeout
	await _shot(chat.window, "3_chat_window")
	_check(chat.window._box.text == "再试一次", "chat window keeps the line too")
	chat.window.hide()

	app.open_workbench()
	await create_timer(1.0).timeout
	var wb: Workbench = app._workbench
	_check(wb._no_model.visible, "workbench shows the no-API banner")
	await _shot(wb, "4_workbench_banner")
	wb.hide()

	# the bubble's link opens the settings at the key
	chat.remind_model()
	await create_timer(0.5).timeout
	chat.bubble._on_click(_click())
	await create_timer(1.5).timeout
	var st: SettingsWindow = app._settings
	_check(st.visible and st._key.has_focus(), "clicking the bubble opens the settings at the key")
	await _shot(st, "5_settings")
	st._key.text = OS.get_environment("ANIMA_PROBE_KEY")
	st._try()
	var t := 0.0
	while st._test.disabled and t < 40.0:
		await create_timer(0.5).timeout
		t += 0.5
	_check(st._result.text.begins_with("连接成功"), "test connection with the real key — " + st._result.text)
	st._store()
	await create_timer(2.0).timeout
	_check(app.model_ready, "saved: the pet knows the model is ready")
	_check(" ".join(chat.bubble._pages).contains("能思考"), "pet says it can think — " + " / ".join(chat.bubble._pages))
	await _shot(st, "6_saved")
	st.hide()
	await create_timer(1.0).timeout
	chat.bubble.dismiss()
	chat.say("下雨天你一般做什么？")
	t = 0.0
	while chat.waiting and t < 60.0:
		await create_timer(0.5).timeout
		t += 0.5
	await create_timer(1.0).timeout
	_check(not chat.bubble._action.is_valid() and chat.bubble._pages.size() > 0,
		"real reply — " + " ".join(chat.bubble._pages))
	await _shot(chat.bubble, "7_real_reply")
	print("model setup smoke: PASS")
	quit(0)

func _click() -> InputEventMouseButton:
	var e := InputEventMouseButton.new()
	e.button_index = MOUSE_BUTTON_LEFT
	e.pressed = true
	return e
