"""Shared acceptance policy for the organization's configured GitHub App client IDs."""

from __future__ import annotations

import re


CLIENT_ID_PATTERN = r"Iv[A-Za-z0-9.]{8,126}"
_CLIENT_ID = re.compile(CLIENT_ID_PATTERN, re.ASCII)

CLIENT_ID_GUARD_RUN = """# Generated from scripts/app_client_id_contract.py.
set -euo pipefail
if [[ ! "$APP_CLIENT_ID" =~ ^Iv[A-Za-z0-9.]{8,126}$ ]]; then
  printf '%s\\n' '::error::GitHub App client ID is not accepted by the configured App policy' >&2
  exit 1
fi
"""


def is_valid_client_id(value: object) -> bool:
    """Return whether a value matches the current configured-App preflight policy."""
    return isinstance(value, str) and _CLIENT_ID.fullmatch(value) is not None
