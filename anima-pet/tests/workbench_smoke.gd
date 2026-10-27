extends SceneTree
## Drives the distillation workbench end to end against a running anima-server (Fake model is
## enough) and saves a screenshot of each stage. Needs a window (not --headless):
##   ANIMA_API=http://127.0.0.1:8032 <Godot> --path . --script res://tests/workbench_smoke.gd -- <out_dir> <source.txt> <name> [aliases]

var out := ""
var wb: Workbench

func _initialize() -> void:
	_run.call_deferred()

func _shot(name: String, frames := 30) -> void:
	for i in frames:
		await process_frame
	await RenderingServer.frame_post_draw
	wb.get_texture().get_image().save_png(out.path_join(name + ".png"))
	print("saved ", name)

func _check(ok: bool, what: String) -> void:
	if not ok:
		printerr("FAIL: ", what, "  status: ", wb._status.text)
		quit(1)

func _until(stage: String, seconds := 60.0) -> void:
	var waited := 0.0
	while str(wb._current.get("stage", "")) != stage and waited < seconds:
		await create_timer(0.5).timeout
		waited += 0.5
	_check(str(wb._current.get("stage", "")) == stage, "stage " + stage)

func _run() -> void:
	var args := OS.get_cmdline_user_args()
	out = args[0]
	DirAccess.make_dir_recursive_absolute(out)
	var app := root.get_node("App")
	app.use_settings("user://test.cfg")
	app.open_workbench()
	wb = app._workbench
	await _shot("00_empty")
	await wb._create()
	await create_timer(1.0).timeout
	_check(str(wb._current.get("stage", "")) == "draft", "created a draft")
	await wb._add_files(PackedStringArray([args[1]]))
	await create_timer(1.0).timeout
	_check(wb._current.sources.size() == 1, "source uploaded")
	wb._name.text = args[2]
	wb._aliases.text = args[3] if args.size() > 3 else ""
	await wb._preview_target()
	await _shot("01_setup")
	await wb._start()
	await _until("review")
	await create_timer(1.0).timeout
	_check(wb._entries.size() > 0, "entries loaded")
	wb._tree.get_root().get_first_child().get_next().get_first_child().select(0)
	await wb._on_evidence_selected(0)
	await _shot("02_review")
	await wb._accept_pending()
	await wb._commit()
	_check(str(wb._current.stage) == "committed", "committed")
	_check(not app.persona_id.is_empty(), "persona remembered")
	await _shot("03_committed")
	print("workbench smoke: PASS (persona ", app.persona_id, ")")
	quit()
