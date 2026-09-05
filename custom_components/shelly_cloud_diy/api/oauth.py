"""Shelly Cloud OAuth login client and the redacting token types.

The documented Cloud Control API authenticates with the account's ``auth_key``
and that is what the poll uses — unchanged. This module exists for the *other*
channel: the cloud WebSocket relay (``api/cloud_ws.py``) accepts an OAuth access
token only, so opting an entry into cloud control means signing in once with the
Shelly account credentials and holding a token afterwards.

The flow, verified against a live throwaway account (2026-07-13):

1. ``POST https://api.shelly.cloud/oauth/login`` — a **fixed** host, not the
   account's server URI. Form ``email`` / ``password=sha1(plaintext)`` /
   ``client_id=shelly-diy`` → ``{isok: true, data: {code}}``, where ``code`` is a
   short-lived JWT whose payload carries the account's own ``user_api_url``.
2. ``POST <user_api_url>/oauth/auth`` — form ``client_id`` / ``code`` → the
   **top-level** ``{access_token, token_type, expires_in, refresh_token}`` shape
   (no ``isok``/``data`` wrapper; that asymmetry is measured, not assumed).

Measured lifecycle facts this module is built on: the access token lives 12 h
and is a JWT with an ``exp`` claim, and the ``refresh_token`` does **not**
rotate — a refresh that omits it is normal and must not strand the account.

Credential handling — canonical statement
-----------------------------------------
``api/cloud_control.py`` makes the claim "here is everywhere your auth_key
goes" checkable with one ``grep``; this module holds itself to the same rule for
the password and the tokens, because they are strictly more powerful (the
password is the account itself). ``docs/AUTH_KEY.md`` is the user-facing
statement; if the rules below change, change that file too.

1. **The plaintext password lives in exactly one function.**
   :func:`sha1_password` hashes it at the flow boundary; every other function
   here takes the digest. Nothing that performs I/O ever sees the plaintext, so
   it cannot be logged, persisted, or put in a traceback by accident.
2. **Never logged, never in an exception message.** Log records carry an HTTP
   status and a path-only URL. Exception messages carry the same. A raw
   ``aiohttp`` error is reported by *type name* only, because
   ``ClientResponseError.__str__`` embeds the full request URL — which on this
   API can carry token material.
3. **Never in a repr.** :class:`OAuthToken` and :class:`ShellyTokenManager`
   override ``__repr__``. A dataclass' generated repr would print the token into
   any f-string, log line or traceback frame summary that touches it.
4. **Nothing is persisted here.** The manager keeps the live token in RAM and
   hands a refreshed one to an optional callback, so the decision of *where* a
   token is stored stays with the wiring layer and out of this module.

Every site that transmits a credential is marked ``# CREDENTIAL:`` below, so the
transmissions can be enumerated by reading rather than by inference. There are
three, all form POSTs, all to the account's own host: the login, the code
exchange, and the refresh.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import aiohttp
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from .cloud_control import (
    ShellyCloudControl,
    ShellyCloudError,
    ShellyCloudRateLimitError,
    ShellyCloudTransportError,
)

if TYPE_CHECKING:
    from aiohttp import ClientSession

_LOGGER = logging.getLogger(__name__)

# Wire facts. They live here rather than in ``const.py`` because this slice is
# the transport only — the wiring pass that adds the option and the entities is
# free to promote them once something else needs them.
OAUTH_LOGIN_URL = "https://api.shelly.cloud/oauth/login"
OAUTH_CLIENT_ID = "shelly-diy"

# Refresh this far ahead of expiry, so a token is renewed before a command needs
# it rather than after one has already failed.
TOKEN_REFRESH_MARGIN_S = 300

# Used only when a token carries neither ``expires_in`` nor a readable ``exp``.
# Short on purpose: a wrong-but-short TTL costs one extra refresh, a
# wrong-but-long one costs a dead session.
TOKEN_FALLBACK_TTL_S = 3600

# Per-request timeout for the two short OAuth round-trips.
_DEFAULT_TIMEOUT_S = 10

# One retry on a rate-limit rejection, then surface it. Matches the interval
# ``cloud_control._post`` settled on: 1.2 s was borderline in testing.
_RATE_LIMIT_BACKOFF_S = 1.5

# Reuse the auth_key client's base-URL normaliser (bare host vs. https:// prefix,
# trailing slash). It is a pure static helper and touches no credential state.
_normalise_base_url = ShellyCloudControl._normalise_base_url  # noqa: SLF001


class ShellyOAuthError(ShellyCloudError, HomeAssistantError):
    """OAuth login, exchange or refresh failed.

    Subclasses :class:`~homeassistant.exceptions.HomeAssistantError` as well as
    the repo's own base so a failure surfaces in the UI and in an automation
    trace instead of only in the log, while ``except ShellyCloudError`` in
    existing callers keeps working.

    The message is always sanitised: an HTTP status and/or a path-only URL,
    never a server body, a token, or a token-bearing query string.
    """


class ShellyOAuthTransportError(ShellyOAuthError, ShellyCloudTransportError):
    """Network-level failure during an OAuth round-trip (DNS, TLS, timeout)."""


class ShellyOAuthRateLimitError(ShellyOAuthError, ShellyCloudRateLimitError):
    """The account's 1 req/s budget rejected the OAuth round-trip."""


@dataclass(frozen=True)
class OAuthToken:
    """One access/refresh token pair, with a **redacting** ``__repr__``.

    ``expires_at`` is absolute epoch seconds, not a duration, so a token that
    has been sitting in memory answers "am I still valid" correctly without
    anyone tracking when it was minted.
    """

    access_token: str
    expires_at: float
    refresh_token: str | None = None

    def expires_within(self, seconds: float) -> bool:
        """True if the token expires within ``seconds`` from now (or already has)."""
        return time.time() >= (self.expires_at - seconds)

    @property
    def is_expired(self) -> bool:
        """True once the token is at or past its expiry epoch."""
        return self.expires_within(0)

    def __repr__(self) -> str:
        # MANDATORY. The generated dataclass repr would print the token itself,
        # and a repr reaches places a deliberate log call never does: f-strings,
        # ``logging`` argument formatting, traceback frame summaries.
        return (
            f"OAuthToken(expires_at={self.expires_at}, "
            f"refresh={'yes' if self.refresh_token else 'no'})"
        )


def sha1_password(plaintext: str) -> str:
    """Hash the plaintext password at the earliest boundary.

    Shelly's ``/oauth/login`` expects ``password=sha1(plaintext)``. This is the
    only function in the integration that sees the plaintext; everything else
    takes the hex digest. That is what makes "the password is never stored and
    never logged" a structural property rather than a promise — the layers that
    do I/O never hold the value in the first place.

    The digest is not a security improvement over the password (it is
    password-equivalent to this API) and is treated with the same care.
    """
    # sha1 is Shelly's choice of wire format, not a hashing decision of ours.
    return hashlib.sha1(plaintext.encode()).hexdigest()  # noqa: S324


def _b64url_json(segment: str) -> dict[str, Any]:
    """Decode one base64url JWT segment into its claims (no signature check).

    We are a client, not a validator: ``exp`` and ``user_api_url`` are read for
    expiry bookkeeping and routing only, never for an authorisation decision.
    """
    segment += "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(segment))


def _decode_expiry(jwt: str, expires_in: Any = None) -> float:
    """Return the absolute expiry epoch for an access token.

    Preference order: an explicit ``expires_in`` from the token response, then
    the JWT ``exp`` claim, then :data:`TOKEN_FALLBACK_TTL_S`. A malformed token
    degrades to the short fallback rather than raising — the caller still holds
    a usable token and simply refreshes sooner.
    """
    now = time.time()
    if expires_in is not None:
        try:
            return now + float(expires_in)
        except (TypeError, ValueError):
            pass
    try:
        exp = _b64url_json(jwt.split(".")[1]).get("exp")
        return float(exp) if exp else now + TOKEN_FALLBACK_TTL_S
    except Exception:  # noqa: BLE001 — malformed JWT → conservative short TTL
        return now + TOKEN_FALLBACK_TTL_S


def _extract_field(body: Any, key: str) -> Any:
    """Read ``key`` from a top-level or ``{data: {...}}``-wrapped body.

    Shelly's two OAuth responses disagree with each other: the login wraps
    ``code`` in ``data``, the token exchange returns the tokens top-level.
    Trying both shapes costs nothing and avoids a silent mis-parse if a future
    firmware aligns them.
    """
    if not isinstance(body, dict):
        return None
    value = body.get(key)
    if value is not None:
        return value
    data = body.get("data")
    if isinstance(data, dict):
        return data.get(key)
    return None


def _server_uri_from_code(code: str) -> str | None:
    """Extract the account's ``user_api_url`` from the login ``code`` JWT."""
    try:
        uri = _b64url_json(code.split(".")[1]).get("user_api_url")
        return uri if isinstance(uri, str) and uri else None
    except Exception:  # noqa: BLE001 — fall back to the caller-supplied host
        return None


async def _post_form(
    session: ClientSession,
    url: str,
    payload: dict[str, str],
    *,
    timeout_s: int = _DEFAULT_TIMEOUT_S,
) -> Any:
    """POST a form body and return parsed JSON, with sanitised error handling.

    Mirrors ``ShellyCloudControl._post`` for status handling and the single
    rate-limit retry, and deliberately diverges on one point: no branch here may
    put a server body into a message. The v1 client can quote Shelly's own error
    text safely because that text never contains the auth_key; an OAuth body
    contains the tokens themselves.

    ``payload`` — which carries the password digest, the login code or the
    refresh token — is never logged, not even at DEBUG.
    """
    safe_url = str(url).split("?", 1)[0]
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    data: Any = None
    for attempt in range(2):
        try:
            async with session.post(url, data=payload, timeout=timeout) as response:
                status = response.status
                if status in (401, 403):
                    # Shelly signals its 1 req/s limit as HTTP 401 with a
                    # ``max_req`` body, so a rejection is only a credential
                    # failure once the body says it is not a rate limit.
                    body_text = await response.text()
                    if ShellyCloudControl._is_rate_limit_body(body_text):  # noqa: SLF001
                        if attempt == 0:
                            await asyncio.sleep(_RATE_LIMIT_BACKOFF_S)
                            continue
                        raise ShellyOAuthRateLimitError("Rate limit exceeded (1 req/s)")
                    _LOGGER.debug("OAuth POST %s rejected: HTTP %s", safe_url, status)
                    raise ShellyOAuthError(f"OAuth request rejected (HTTP {status})")
                if status == 429:
                    if attempt == 0:
                        await asyncio.sleep(_RATE_LIMIT_BACKOFF_S)
                        continue
                    raise ShellyOAuthRateLimitError("Rate limit exceeded (1 req/s)")
                response.raise_for_status()
                data = await response.json(content_type=None)
                break
        except asyncio.TimeoutError as err:
            raise ShellyOAuthTransportError(f"Timeout calling {safe_url}") from err
        except aiohttp.ClientError as err:
            # NEVER interpolate ``err``: ClientResponseError embeds the full
            # request URL in ``__str__``, and on this API the URL can carry the
            # token. The type name is enough to tell DNS from TLS from reset.
            raise ShellyOAuthTransportError(
                f"HTTP error calling {safe_url} ({type(err).__name__})"
            ) from err
    _LOGGER.debug("OAuth POST %s ok", safe_url)
    return data


async def request_code(session: ClientSession, email: str, password_sha1: str) -> str:
    """Sign in at the fixed ``/oauth/login`` host and return the single-use code.

    ``password_sha1`` is the digest from :func:`sha1_password`; the plaintext
    never reaches this layer. A response without a ``code`` raises
    :class:`ShellyOAuthError` rather than returning ``None``, so a renamed field
    fails loudly at setup instead of half-configuring an entry.
    """
    # CREDENTIAL (sent 1/3): the password digest, as a form field, to the fixed
    # Shelly login host. This is the only request in the integration that
    # carries anything derived from the account password. (docs/AUTH_KEY.md)
    payload = {
        "email": email,
        "password": password_sha1,
        "client_id": OAUTH_CLIENT_ID,
    }
    body = await _post_form(session, OAUTH_LOGIN_URL, payload)
    code = _extract_field(body, "code")
    if not isinstance(code, str) or not code:
        raise ShellyOAuthError("OAuth login response carried no 'code'")
    return code


async def exchange_code(session: ClientSession, server_uri: str, code: str) -> OAuthToken:
    """Exchange the login ``code`` at ``<server_uri>/oauth/auth`` for a token."""
    base = _normalise_base_url(server_uri)
    url = f"{base}/oauth/auth"
    # CREDENTIAL (sent 2/3): the single-use login code, as a form field, to the
    # account's own host — the one named by the code's ``user_api_url`` claim.
    payload = {"client_id": OAUTH_CLIENT_ID, "code": code}
    body = await _post_form(session, url, payload)

    access_token = _extract_field(body, "access_token")
    if not isinstance(access_token, str) or not access_token:
        raise ShellyOAuthError("OAuth token response carried no 'access_token'")
    refresh = _extract_field(body, "refresh_token")
    return OAuthToken(
        access_token=access_token,
        expires_at=_decode_expiry(access_token, _extract_field(body, "expires_in")),
        refresh_token=refresh if isinstance(refresh, str) and refresh else None,
    )


async def login(
    session: ClientSession,
    server_uri: str,
    email: str,
    password_sha1: str,
) -> OAuthToken:
    """Run the full sign-in: :func:`request_code` then :func:`exchange_code`.

    The login code's ``user_api_url`` claim takes precedence over the caller's
    ``server_uri``: the exchange must hit the account's own regional host, and
    the account itself is the authority on which one that is. The caller's value
    is the fallback for a code we could not read.
    """
    code = await request_code(session, email, password_sha1)
    return await exchange_code(session, _server_uri_from_code(code) or server_uri, code)


async def refresh_token(
    session: ClientSession, server_uri: str, refresh_token: str
) -> OAuthToken:
    """Exchange a stored refresh token for a fresh :class:`OAuthToken`.

    The refresh posts ``grant_type=refresh_token`` to the same ``/oauth/auth``
    endpoint the code exchange uses. Measured against the live test account: the
    ``refresh_token`` does **not** rotate, so a response that omits one is the
    normal case and the caller's durable secret is carried forward. Treating a
    missing rotation as "the token is gone" would strand the account after the
    first refresh.
    """
    base = _normalise_base_url(server_uri)
    url = f"{base}/oauth/auth"
    # CREDENTIAL (sent 3/3): the refresh token, as a form field, to the
    # account's own host. Same destination as the exchange above.
    payload = {
        "client_id": OAUTH_CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    body = await _post_form(session, url, payload)

    access_token = _extract_field(body, "access_token")
    if not isinstance(access_token, str) or not access_token:
        raise ShellyOAuthError("OAuth refresh response carried no 'access_token'")
    rotated = _extract_field(body, "refresh_token")
    return OAuthToken(
        access_token=access_token,
        expires_at=_decode_expiry(access_token, _extract_field(body, "expires_in")),
        refresh_token=rotated if isinstance(rotated, str) and rotated else refresh_token,
    )


class ShellyTokenManager:
    """Holds the live OAuth token for one config entry and renews it.

    Two jobs, both about not surprising the user:

    * **Serialise.** Every refresh runs under one lock, so a reconnect storm or
      several entities asking at once produce a single round-trip rather than a
      burst against a 1 req/s budget.
    * **Escalate honestly.** With no usable refresh token, or after the server
      rejects the one we hold, the manager raises
      :class:`~homeassistant.exceptions.ConfigEntryAuthFailed` so Home Assistant
      asks the user to sign in again. There is deliberately no
      password-at-rest rung to fall back on: the password is never stored, so a
      dead refresh token genuinely means "ask the human".

    Persistence is the caller's business. The manager keeps the token in RAM and
    hands each new one to ``on_token_refreshed``; deciding where it is written
    stays outside the module that holds the secret.
    """

    def __init__(
        self,
        session: ClientSession,
        server_uri: str,
        *,
        token: OAuthToken | None = None,
        on_token_refreshed: Callable[[OAuthToken], Awaitable[None]] | None = None,
    ) -> None:
        """Bind the manager to one account.

        Args:
            session: shared aiohttp session used for the refresh round-trip.
            server_uri: the account's server URI (bare host or full URL).
            token: the token from the initial :func:`login`, or one restored
                from storage. Without it the first call escalates to reauth.
            on_token_refreshed: awaited with every newly minted token, for the
                caller to persist. An exception from it is logged and swallowed
                — a storage failure must not take the live session down.
        """
        self._session = session
        self._server_uri = server_uri
        self._token = token
        self._on_token_refreshed = on_token_refreshed
        self._lock = asyncio.Lock()
        self._auth_failed = False

    @property
    def auth_failed(self) -> bool:
        """True once a refresh failure has escalated to reauth."""
        return self._auth_failed

    @property
    def expires_at(self) -> float | None:
        """Expiry epoch of the held token, for diagnostics. Never the token."""
        return self._token.expires_at if self._token else None

    async def async_get_token(self) -> str:
        """Return a valid access token, refreshing ahead of expiry.

        Serves the cached token unless it is within
        :data:`TOKEN_REFRESH_MARGIN_S` of expiring, so the ordinary case costs
        no network traffic at all.
        """
        async with self._lock:
            if self._token is None or self._token.expires_within(TOKEN_REFRESH_MARGIN_S):
                await self._refresh_locked()
            assert self._token is not None  # set by _refresh_locked, or it raised
            return self._token.access_token

    async def async_force_refresh(self) -> str:
        """Mint a fresh token unconditionally and return it.

        Bound to the relay's token-rejected hook. A rejection means the current
        access token is not accepted, so re-serving it would spin; only a new
        one can break the loop.
        """
        async with self._lock:
            await self._refresh_locked()
            assert self._token is not None
            return self._token.access_token

    async def _refresh_locked(self) -> None:
        """Refresh the token; the caller holds the lock."""
        stored_refresh = self._token.refresh_token if self._token else None
        if not stored_refresh:
            # No durable credential and no password at rest: the only honest
            # move is to ask the user.
            self._auth_failed = True
            raise ConfigEntryAuthFailed(
                "No Shelly refresh token available; sign in again"
            )
        try:
            self._token = await refresh_token(
                self._session, self._server_uri, stored_refresh
            )
        except ShellyOAuthRateLimitError:
            # A rate limit says nothing about the credential. Keep the old token
            # and let the caller retry rather than pushing the user into reauth.
            raise
        except ShellyOAuthError as err:
            self._auth_failed = True
            raise ConfigEntryAuthFailed(str(err)) from err
        self._auth_failed = False
        if self._on_token_refreshed is not None:
            try:
                await self._on_token_refreshed(self._token)
            except Exception as err:  # noqa: BLE001 — type name only, never a token
                _LOGGER.error(
                    "Storing the refreshed Shelly token failed: %s", type(err).__name__
                )

    def __repr__(self) -> str:
        # MANDATORY, same reasoning as OAuthToken: the default repr of an object
        # holding a token is one f-string away from a log line.
        return f"ShellyTokenManager(expires_at={self.expires_at})"
