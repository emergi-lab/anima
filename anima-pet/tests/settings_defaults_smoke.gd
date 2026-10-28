extends SceneTree
## Offline settings UI regression: no backend requests, credentials or user-setting writes.

var failed := false

func _initialize() -> void:
	_run.call_deferred()

func _check(condition: bool, message: String) -> void:
	if not condition:
		failed = true
		push_error(message)

func _run() -> void:
	var window := SettingsWindow.new()
	window.app = root.get_node("App")
	root.add_child(window)
	window.hide()
	var fresh := {"backend": "openai", "base_url": "", "model": "",
		"disable_thinking": true, "key_set": false, "ready": false,
		"key_hint": "", "source": "env"}
	window._show_model(fresh)
	_check(window._url.text.is_empty(), "Fresh URL must be empty")
	_check(window._model.text.is_empty(), "Fresh model must be empty")
	_check(window._key.text.is_empty(), "The API key must never be prefilled")
	_check(not window._valid_model(), "An empty URL must be rejected before sending")
	window._url.text = "https://example.invalid/v1"
	_check(not window._valid_model(), "An empty model must be rejected before sending")
	window._model.text = "  "
	_check(not window._valid_model(), "A whitespace-only model must be rejected")
	window._model.text = " my-model "
	_check(window._valid_model(), "Explicit URL and model must be accepted")
	_check(window._body().model == "my-model", "Submitted model must be trimmed")
	var saved := fresh.duplicate()
	saved.merge({"base_url": "https://example.invalid/v1", "model": "saved-model",
		"key_set": true, "key_hint": "…abcd", "ready": true, "source": "saved"}, true)
	window._show_model(saved)
	_check(window._url.text == saved.base_url, "A saved URL must not be cleared")
	_check(window._model.text == saved.model, "A saved model must not be cleared")
	_check(window._key.text.is_empty(), "A saved key must not be shown")
	window.queue_free()
	await process_frame
	print("settings defaults smoke: ", "FAIL" if failed else "PASS")
	quit(1 if failed else 0)
