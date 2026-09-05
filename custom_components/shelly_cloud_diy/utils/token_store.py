"""Turning an OAuth token into something ``entry.data`` can hold, and back.

Deliberately its own module rather than a pair of helpers inside the config
flow or the setup entry point, for the reason ``api/oauth.py`` states about
itself: the module that *holds* a credential must not know where it is
stored. This is the other half of that split — the layer that knows the
storage shape and nothing about how a token is minted or refreshed. Both the
config flow (which writes the first one) and the setup path (which reads it
back and writes a rotated one) need the same shape, and one definition is how
the two stay in agreement.

Nothing here logs. A malformed record returns ``None`` and the caller asks
the user to sign in again, because the alternative — reporting what was
wrong with it — means describing a token in a log line.
"""
from __future__ import annotations

from typing import Any

from ..api.oauth import OAuthToken

# Sub-keys of the stored record. They match the wire field names, which keeps
# a diagnostics reader honest: this is a token, not an opaque blob.
KEY_ACCESS = "access_token"  # noqa: S105 — a key name, not a secret
KEY_REFRESH = "refresh_token"  # noqa: S105 — a key name, not a secret
KEY_EXPIRES_AT = "expires_at"  # noqa: S105 — a key name, not a secret


def token_to_storage(token: OAuthToken) -> dict[str, Any]:
    """Return the JSON-serialisable record for one token."""
    return {
        KEY_ACCESS: token.access_token,
        KEY_REFRESH: token.refresh_token,
        KEY_EXPIRES_AT: token.expires_at,
    }


def token_from_storage(raw: Any) -> OAuthToken | None:
    """Rebuild a token from ``entry.data``, or ``None`` if it is unusable.

    "Unusable" is judged on the access token alone. A record without a
    refresh token is still worth loading: it works until it expires, and the
    manager then escalates to re-authentication, which is the honest outcome
    and better than refusing a session the user could still have used.
    """
    if not isinstance(raw, dict):
        return None
    access = raw.get(KEY_ACCESS)
    if not isinstance(access, str) or not access:
        return None
    refresh = raw.get(KEY_REFRESH)
    expires_at = raw.get(KEY_EXPIRES_AT)
    try:
        expires_at = float(expires_at)
    except (TypeError, ValueError):
        # An unreadable expiry means "refresh at the first opportunity",
        # never "valid forever".
        expires_at = 0.0
    return OAuthToken(
        access_token=access,
        expires_at=expires_at,
        refresh_token=refresh if isinstance(refresh, str) and refresh else None,
    )
