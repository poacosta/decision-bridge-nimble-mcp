import pytest

from decision_bridge.errors import BridgeError, ErrorCode, classify_retryable


def test_payload_shape():
    err = BridgeError(
        ErrorCode.MODEL_NOT_FOUND, "The configured model is not installed.", request_id="r1"
    )
    assert err.to_payload() == {
        "error": {
            "code": "MODEL_NOT_FOUND",
            "message": "The configured model is not installed.",
            "retryable": False,
            "request_id": "r1",
        }
    }


def test_all_thirteen_codes_exist():
    assert len(ErrorCode) == 13


@pytest.mark.parametrize(
    "code", [ErrorCode.INVALID_INPUT, ErrorCode.PAYLOAD_TOO_LARGE, ErrorCode.INVALID_CONFIGURATION]
)
def test_input_and_config_errors_exit_2(code):
    assert BridgeError(code, "x").exit_code == 2


@pytest.mark.parametrize(
    "code", [ErrorCode.OLLAMA_UNAVAILABLE, ErrorCode.TIMEOUT, ErrorCode.UPSTREAM_ERROR]
)
def test_service_errors_exit_1(code):
    assert BridgeError(code, "x").exit_code == 1


@pytest.mark.parametrize("code", [ErrorCode.TIMEOUT, ErrorCode.BUSY, ErrorCode.OLLAMA_UNAVAILABLE])
def test_transient_codes_are_retryable(code):
    assert classify_retryable(code) is True
    assert BridgeError(code, "x").retryable is True


def test_upstream_error_retryable_only_for_gateway_style_statuses():
    assert classify_retryable(ErrorCode.UPSTREAM_ERROR, 503) is True
    assert classify_retryable(ErrorCode.UPSTREAM_ERROR, 500) is False
    assert classify_retryable(ErrorCode.UPSTREAM_ERROR, 400) is False
    assert classify_retryable(ErrorCode.UPSTREAM_ERROR, None) is False


def test_explicit_retryable_overrides_default():
    assert BridgeError(ErrorCode.TIMEOUT, "x", retryable=False).retryable is False
