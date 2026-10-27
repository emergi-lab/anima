class_name Backend
extends Node
## HTTP JSON client for anima-server, which only listens on this machine.

var base_url := _env("ANIMA_API", "http://127.0.0.1:8030")
var token := _env("ANIMA_TOKEN", "anima-local-token")

static func _env(name: String, fallback: String) -> String:
	var value := OS.get_environment(name)
	return value if not value.is_empty() else fallback

func headers() -> PackedStringArray:
	return PackedStringArray(["Content-Type: application/json", "Authorization: Bearer " + token])

## Model calls take a while (a chat answer is two calls, about 15 s with flash).
func request_json(path: String, method: int = HTTPClient.METHOD_GET, body: Variant = null,
		timeout := 120.0) -> Dictionary:
	var request := HTTPRequest.new()
	request.timeout = timeout
	add_child(request)
	var payload := "" if body == null else JSON.stringify(body)
	var error := request.request(base_url.trim_suffix("/") + path, headers(), method, payload)
	if error != OK:
		request.queue_free()
		return {"ok": false, "error": "无法发起 HTTP 请求。"}
	var response = await request.request_completed
	request.queue_free()
	if response[0] != HTTPRequest.RESULT_SUCCESS:
		return {"ok": false, "error": "连接失败或超时，请检查 anima-server 是否已启动。"}
	var code := int(response[1])
	var text: String = response[3].get_string_from_utf8()
	var data = JSON.parse_string(text) if not text.is_empty() else null
	if code < 200 or code >= 300:
		# anima-server answers {"error": {"code", "message", "retryable"}}; FastAPI's own 404/405
		# answer {"detail": ...}.
		var err = data.get("error") if data is Dictionary else null
		if err is Dictionary:
			return {"ok": false, "status": code, "code": str(err.get("code", "")),
				"retryable": bool(err.get("retryable", false)),
				"error": "HTTP %d  %s" % [code, str(err.get("message", ""))]}
		var detail = data.get("detail", "") if data is Dictionary else ""
		return {"ok": false, "status": code, "error": "HTTP %d  %s" % [code, detail if detail is String else "请求字段校验失败"]}
	return {"ok": true, "status": code, "data": data}

func health() -> Dictionary:
	return await request_json("/v1/runtime")
