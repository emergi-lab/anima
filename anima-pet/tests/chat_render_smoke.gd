extends SceneTree
## Standalone native-window visual check; no model calls or user-setting writes.
## Godot --path anima-pet --script res://tests/chat_render_smoke.gd
## Close the preview window to finish. Use -- --legacy to compare the old behavior.

func _initialize() -> void:
	_run.call_deferred()

func _run() -> void:
	var app := root.get_node("App")
	root.title = "Anima - Chat rendering check"
	root.transparent = false
	root.transparent_bg = false
	root.borderless = false
	root.unresizable = false
	root.always_on_top = true
	root.size = Vector2i(760, 300)
	var area := DisplayServer.screen_get_usable_rect()
	root.position = area.position + (area.size - root.size) / 2
	root.close_requested.connect(func() -> void: quit())
	var background := ColorRect.new()
	background.color = UI.C_BG
	background.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	root.add_child(background)
	var heading := UI.label("聊天渲染检查：输入条、中文回复和按钮应清晰可见。", 18)
	heading.position = Vector2(20, 20)
	root.add_child(heading)
	var input := ChatInput.new()
	input.app = app
	input.theme = app.theme
	var bubble := ChatBubble.new()
	bubble.app = app
	bubble.theme = app.theme
	if "--legacy" in OS.get_cmdline_user_args():
		input.borderless = true
		bubble.borderless = true
		bubble.unfocusable = true
		input.transparent = true
		input.transparent_bg = true
		bubble.transparent = true
		bubble.transparent_bg = true
	root.add_child(input)
	root.add_child(bubble)
	input.content_scale_factor = 1.25
	input.size = Vector2i(Vector2(ChatInput.BASE_SIZE) * 1.25)
	input.position = root.position + Vector2i(20, 180)
	input.open_for("测试")
	input._line.text = "你好，聊天文字显示测试"
	bubble.content_scale_factor = 1.25
	bubble.say("你好，这是一条中文回复。")
	bubble._page_typed()
	bubble.set_process(false)
	bubble.position = root.position + Vector2i(20, 80)
	print("chat render preview: Windows=", OS.get_name() == "Windows",
		" input_transparent=", input.transparent, " bubble_transparent=", bubble.transparent)
	await create_timer(1.0).timeout
	# Capture the desktop composite: viewport textures alone miss native-window bugs.
	if DisplayServer.has_feature(DisplayServer.FEATURE_SCREEN_CAPTURE):
		var shot := DisplayServer.screen_get_image(root.current_screen)
		var origin := root.position - DisplayServer.screen_get_position(root.current_screen)
		var path := "user://chat-render-legacy.png" if "--legacy" in OS.get_cmdline_user_args() else "user://chat-render-fixed.png"
		shot.get_region(Rect2i(origin, root.size)).save_png(path)
		print("native screenshot: ", ProjectSettings.globalize_path(path))
