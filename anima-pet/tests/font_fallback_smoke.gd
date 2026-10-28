extends SceneTree
## No model or user settings: verify bundled fonts and the macOS fallback graph.

func _initialize() -> void:
	var body := UI.body_font() as FontFile
	var pixel := UI.pixel_font() as FontFile
	assert(body != null and pixel != null)
	for character in "Anima 桌宠设置蒸馏人物":
		assert(body.has_char(character.unicode_at(0)), "Missing body glyph: " + character)
	assert(pixel.fallbacks[0] == body)
	if OS.get_name() == "macOS":
		assert(not body.allow_system_fallback and not pixel.allow_system_fallback)
		var fallback := body.fallbacks[0] as FontFile
		assert(fallback != null and fallback != pixel and fallback != body)
		assert(fallback.fallbacks.is_empty() and not fallback.allow_system_fallback)
	print("font fallback: PASS")
	quit()
