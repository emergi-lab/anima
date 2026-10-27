extends SceneTree
## Compile every script under res://scripts and res://tests (parse errors print).
func _initialize() -> void:
	var bad := 0
	for dir in ["res://scripts", "res://tests"]:
		for file in DirAccess.get_files_at(dir):
			if file.ends_with(".gd"):
				var script := load(dir.path_join(file)) as GDScript
				if script == null or not script.can_instantiate():
					bad += 1
					printerr("BROKEN: ", file)
	print("scripts checked: ", "PASS" if bad == 0 else "FAIL %d" % bad)
	quit(0 if bad == 0 else 1)
