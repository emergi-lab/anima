from __future__ import annotations


class ServiceError(Exception):
    """Error surfaced to callers in the contract format {"error": {...}}."""

    def __init__(self, status: int, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable


def not_implemented(stage: str) -> ServiceError:
    return ServiceError(501, "not_implemented", f"planned for {stage}; see docs/未修复.md")


SETTINGS_HINT = "请在桌宠的「设置 → 模型服务」里填写或检查"
# ModelCallError kinds that no retry can fix: the owner's model service turned the request down
REJECTED = {"auth": "API Key 不对或没有权限", "quota": "额度不足或已欠费",
            "not_found": "接口地址或模型名不对"}
# how the two messages below begin, to recognise them once stored as a job's error text
REFUSALS = ("还没有配置模型 API", "模型服务拒绝了请求")


def model_not_configured() -> ServiceError:
    return ServiceError(412, "model_not_configured", f"还没有配置模型 API。{SETTINGS_HINT}")


def model_rejected(kind: str, detail: str) -> ServiceError:
    return ServiceError(503, "model_rejected",
                        f"模型服务拒绝了请求：{REJECTED[kind]}（{detail}）。{SETTINGS_HINT}")
