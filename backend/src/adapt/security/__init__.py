"""Security boundary helpers.

The package is imported by API dependencies and model clients. Load console-only
settings into the already-cached Settings instance before service construction, then
install the safe LogRecordFactory so runtime failures cannot persist a configured
provider/key in logs. Public HTTP/WebSocket redaction is installed by the existing
security-headers middleware. Remaining integration notes are in
``docs/AUDIT.md``, which carries the security findings.
"""

from __future__ import annotations


def _configure_security() -> None:
    from ..config import get_settings
    from ..config_secrets import _ALL_MANAGED_PAIRS, settings_with_managed_values
    from .secrecy import install_log_redaction

    current = get_settings()
    effective = settings_with_managed_values(current)
    # get_settings is cached; update its single object in-place so services created
    # after this import observe the same secret source as the console.
    for _env_name, field_name in _ALL_MANAGED_PAIRS:
        setattr(current, field_name, getattr(effective, field_name))
    install_log_redaction(current)


_configure_security()
