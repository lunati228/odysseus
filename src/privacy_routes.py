"""HTTP entry-point policy for Privacy Workspace (PRV-003).

Service guards remain the final authority, but an authenticated caller should
not be able to manually reactivate a capability merely because its startup
job or UI control is disabled.  This middleware rejects capability families
at the ASGI boundary before route dependencies, request bodies, integrations,
or subprocess helpers are reached.
"""
from __future__ import annotations

from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.privacy_mode import current_profile, is_privacy_mode, normalize_profile
from src.privacy_policy import CapabilityDenied


# Each prefix is matched on a path-component boundary.  Coarse families are
# intentional: Privacy Workspace does not need configuration/status endpoints
# for capabilities it cannot execute, and omitting the whole router family is
# easier to audit than maintaining an action-by-action denylist.
_DENIED_PREFIXES: tuple[tuple[str, str], ...] = (
    ("/api/shell", "shell-automation"),
    ("/api/cookbook", "shell-automation"),
    ("/api/mcp", "network-mcp"),
    ("/api/webhooks", "webhooks"),
    ("/api/email", "email-sync"),
    ("/api/copilot", "cloud-models"),
    ("/api/chatgpt-subscription", "cloud-models"),
    ("/api/tasks", "background-automations"),
    ("/api/assistant", "background-automations"),
    ("/api/codex", "extension-execution"),
    ("/api/claude", "extension-execution"),
    ("/api/companion", "remote-notifications"),
    ("/api/vault", "extension-execution"),
    ("/api/auth/integrations", "direct-http"),
    ("/api/calendar/config", "calendar-sync"),
    ("/api/contacts/config", "contacts-sync"),
)

_DENIED_EXACT: dict[str, str] = {
    "/api/model/download": "model-downloads",
    "/api/model/serve": "shell-automation",
    "/api/v1/chat": "webhooks",
    "/api/calendar/test": "calendar-sync",
    "/api/calendar/sync": "calendar-sync",
    "/api/discover": "direct-http",
    "/api/skills/import-from-url": "skill-import",
}


def _prefix_matches(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def privacy_route_capability(
    path: str,
    method: str,
    *,
    profile: Optional[str] = None,
) -> Optional[str]:
    """Return the refused capability for a request, otherwise ``None``."""
    if not is_privacy_mode(profile):
        return None

    normalized_path = str(path or "")
    capability = _DENIED_EXACT.get(normalized_path)
    if capability:
        return capability

    for prefix, capability in _DENIED_PREFIXES:
        if _prefix_matches(normalized_path, prefix):
            return capability

    # Keep cached local FastEmbed model inspection/removal available, but do
    # not let the download action fetch a Hugging Face artifact.
    if (
        str(method or "").upper() == "POST"
        and normalized_path.startswith("/api/embeddings/models/")
        and normalized_path.endswith("/download")
    ):
        return "model-downloads"

    return None


class PrivacyRoutePolicyMiddleware(BaseHTTPMiddleware):
    """Reject disabled route families before their handlers execute."""

    def __init__(self, app, *, profile: Optional[str] = None):
        super().__init__(app)
        self._profile = profile

    async def dispatch(self, request, call_next):
        capability = privacy_route_capability(
            request.url.path,
            request.method,
            profile=self._profile,
        )
        if capability is None:
            return await call_next(request)

        selected = (
            current_profile()
            if self._profile is None
            else normalize_profile(self._profile)
        )
        denial = CapabilityDenied(capability, selected)
        return JSONResponse({"detail": str(denial)}, status_code=403)


__all__ = ["PrivacyRoutePolicyMiddleware", "privacy_route_capability"]
