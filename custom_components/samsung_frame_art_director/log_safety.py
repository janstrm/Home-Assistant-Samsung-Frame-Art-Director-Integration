"""Keep TV credentials out of logs even when HA enables dependency DEBUG."""

from __future__ import annotations

import logging
import re

# Covers dict/JSON fields, escaped JSON inside TV frames, and query parameters.
# Do not match safe diagnostics such as token_present=True.
_CREDENTIAL_FIELD = re.compile(
    r"\b(?:(?:access[_-]?)?token|authsig|api[_-]?key|authorization)[\\\"'\s]*[:=]",
    re.IGNORECASE,
)
_PREFIXES = ("samsungtvws", "custom_components.samsung_frame_art_director", "samsung_frame_art_director")
_SDK_MODULES = (
    "connection", "helper", "rest", "remote", "art", "art.art",
    "async_connection", "async_remote", "async_art", "encrypted", "encrypted.remote",
)
_INTEGRATION_MODULES = (
    "api", "art_connection", "bridge", "config_flow", "curator", "ai",
    "ip_control", "ip_control_pairing", "ip_control_actions", "media_player",
    "image", "number", "select", "sensor", "switch", "text",
)


class CredentialLogFilter(logging.Filter):
    """Discard credential-bearing payloads, not useful surrounding diagnostics."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if _CREDENTIAL_FIELD.search(message) or (
            record.name.startswith("samsungtvws")
            and re.search(r"\btoken\b", message, re.IGNORECASE)
        ):
            # Payloads can contain nested/escaped JSON or multiline values.
            # Omitting the complete payload is safer than guessing its syntax.
            record.msg = "[Credential-bearing TV log payload redacted]"
            record.args = ()
        exception = record.exc_text
        if record.exc_info:
            exception = logging.Formatter().formatException(record.exc_info)
        if exception and _CREDENTIAL_FIELD.search(exception):
            record.exc_info = None
            record.exc_text = "[Credential-bearing TV exception details redacted]"
        if record.stack_info and _CREDENTIAL_FIELD.search(record.stack_info):
            record.stack_info = "[Credential-bearing stack details redacted]"
        return True


_FILTER = CredentialLogFilter()


def install_credential_log_filters() -> None:
    """Attach at source loggers; parent logger filters do not cover children."""
    names = set(_PREFIXES)
    names.update(f"samsungtvws.{suffix}" for suffix in _SDK_MODULES)
    for prefix in _PREFIXES[1:]:
        names.update(f"{prefix}.{suffix}" for suffix in _INTEGRATION_MODULES)
    names.update(
        name for name in tuple(logging.Logger.manager.loggerDict)
        if any(name.startswith(f"{prefix}.") for prefix in _PREFIXES)
    )
    for name in names:
        logger = logging.getLogger(name)
        if _FILTER not in logger.filters:
            logger.addFilter(_FILTER)
