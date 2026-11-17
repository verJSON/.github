"""Verifier-only contract for managed-caller delivery previews.

verjson-ci owns the delivery adapter (ARB Option A on .github#1965). This
module never writes callers. Bootstrap may inspect a delivery preview, but
``readyForApply: false`` (including held draft verjson-ci#333 previews) must
never authorize apply. True apply waits on a sealed verjson-ci writer.
"""

from __future__ import annotations

from typing import Any

CALLER_DELIVERY_HOLD = (
    "caller delivery is unavailable until conflict-safe ownership is implemented"
)
PREVIEW_NOT_READY = "caller delivery preview is not ready for apply"
PREVIEW_MALFORMED = "caller delivery preview is malformed"


class DeliveryVerifyError(RuntimeError):
    pass


def require_delivery_preview_shape(preview: Any) -> dict[str, Any]:
    if not isinstance(preview, dict) or "readyForApply" not in preview:
        raise DeliveryVerifyError(PREVIEW_MALFORMED)
    if type(preview["readyForApply"]) is not bool:
        raise DeliveryVerifyError(PREVIEW_MALFORMED)
    return preview


def require_delivery_preview_ready_for_apply(preview: Any) -> dict[str, Any]:
    """Reject non-authorizing previews. Does not authorize bootstrap writes."""
    checked = require_delivery_preview_shape(preview)
    if checked["readyForApply"] is not True:
        raise DeliveryVerifyError(PREVIEW_NOT_READY)
    return checked


def reject_bootstrap_caller_apply(
    *,
    callers: list[Any],
    delivery_preview: Any | None = None,
) -> None:
    """Fail closed before any caller mutation.

    Organization-only manifests (empty callers) pass. Non-empty callers always
    raise the delivery hold. When a preview is supplied, a false or malformed
    ``readyForApply`` is rejected explicitly so draft previews cannot be
    mistaken for apply authority.
    """
    if not callers:
        return
    if delivery_preview is not None:
        require_delivery_preview_ready_for_apply(delivery_preview)
    raise DeliveryVerifyError(CALLER_DELIVERY_HOLD)
