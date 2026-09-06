import json
import logging
import re
from datetime import date, datetime, time, timezone
from enum import Enum


SENSITIVE_KEYS = {
    "access_key",
    "api_key",
    "authorization",
    "cookie",
    "cookies",
    "connection_string",
    "database_url",
    "db_url",
    "dsn",
    "jwt",
    "password",
    "passwd",
    "private_key",
    "r2_access_key_id",
    "r2_secret_access_key",
    "secret",
    "secret_key",
    "signed_url",
    "token",
}
_STANDARD_LOG_RECORD_KEYS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)
_BEARER_RE = re.compile(r"(?i)(\bbearer\s+)[^\s,;]+")
_DATABASE_URL_RE = re.compile(
    r"(?i)\b((?:postgres(?:ql)?|mysql|mariadb|redis(?:s)?|amqps?|mongodb(?:\+srv)?)(?:\+[^:/\s]+)?://[^:/\s]+:)([^@\s]+)(@)"
)
_HEADER_VALUE_RE = re.compile(
    r"(?im)\b(cookie|set-cookie|authorization|proxy-authorization)\s*[:=]\s*[^\r\n]+"
)
_QUOTED_KEY_VALUE_RE = re.compile(
    r"(?i)([\"']?\b(?:password|passwd|secret|token|api[_-]?key)\b[\"']?\s*[:=]\s*)([\"'])(.*?)\2"
)
_KEY_VALUE_RE = re.compile(
    r"(?i)([\"']?\b(?:authorization|cookie|jwt|password|passwd|secret|token|api[_-]?key)\b[\"']?\s*[:=]\s*)[^\s,;\}\]]+"
)
_SIGNED_URL_RE = re.compile(
    r"(?i)https?://[^\s<>\"']*(?:x-amz-|signature=|sig=|access[_-]?token=|token=|secret=)[^\s<>\"']*"
)


class JsonLogFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _format_record_message(record),
        }
        if record.exc_info:
            payload["exception"] = record.exc_info[0].__name__
        for key, value in record.__dict__.items():
            if key in _STANDARD_LOG_RECORD_KEYS or key.startswith("_"):
                continue
            payload[key] = _sanitize_log_value(key, value)
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _sanitize_text(value: str) -> str:
    value = _DATABASE_URL_RE.sub(r"\1[REDACTED]\3", str(value))
    value = _HEADER_VALUE_RE.sub(lambda match: f"{match.group(1)}: [REDACTED]", value)
    value = _SIGNED_URL_RE.sub("[REDACTED_URL]", value)
    value = _BEARER_RE.sub(r"\1[REDACTED]", value)
    value = _QUOTED_KEY_VALUE_RE.sub(r"\1\2[REDACTED]\2", value)
    return _KEY_VALUE_RE.sub(r"\1[REDACTED]", value)


def _format_record_message(record) -> str:
    safe_args = _sanitize_message_args(record.args)
    message = record.msg
    if isinstance(message, str):
        if safe_args:
            try:
                message = message % safe_args
            except (TypeError, ValueError):
                message = f"{message} {_safe_string(safe_args)}"
    else:
        message = _safe_string(_sanitize_log_value("message", message))
    return _sanitize_text(message)


def _sanitize_message_args(args):
    if isinstance(args, dict):
        return {key: _sanitize_message_argument(value) for key, value in args.items()}
    if isinstance(args, tuple):
        return tuple(_sanitize_message_argument(value) for value in args)
    return _sanitize_message_argument(args)


def _sanitize_message_argument(value):
    if isinstance(value, BaseException):
        return {"type": type(value).__name__}
    if hasattr(value, "META") or hasattr(value, "COOKIES"):
        return {"type": type(value).__name__}
    return _sanitize_log_value("message_argument", value)


def _safe_string(value) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=True, default=lambda item: {"type": type(item).__name__})
    except (TypeError, ValueError):
        return f"<{type(value).__name__}>"


def _sanitize_log_value(key, value):
    lowered = str(key).lower().replace("-", "_")
    if any(sensitive in lowered for sensitive in SENSITIVE_KEYS):
        return "[REDACTED]"
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Enum):
        return _sanitize_text(value.value)
    if isinstance(value, BaseException):
        return {"type": type(value).__name__}
    if isinstance(value, dict):
        return {
            str(nested_key): _sanitize_log_value(nested_key, nested_value)
            for nested_key, nested_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_log_value(key, item) for item in value]
    if hasattr(value, "META") or hasattr(value, "COOKIES"):
        return {"type": type(value).__name__}
    return {"type": type(value).__name__}
