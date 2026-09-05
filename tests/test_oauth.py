"""Unit tests for the Shelly Cloud OAuth client and its token manager.

Two things are being protected here, and only one of them is behaviour.

**Behaviour.** Every failure path must raise a typed exception rather than hand
back ``None``. The consumer of this module is a control channel for water
valves; a caller that cannot tell a token apart from the absence of one will
happily report a command as sent.

**Secrecy.** The password digest and the tokens must not reach a log record, an
exception message or a ``repr``. That is not something a reviewer can keep true
by reading — a dataclass' generated ``repr`` leaks a token into any f-string
that touches it, and ``aiohttp``'s own errors carry the request URL in their
message. So the tests drive the real code with a fake transport and then search
everything it produced for the credential literals.

Every credential in this file is a fake literal. No network, no running Home
Assistant, no real account.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.shelly_cloud_diy.api import oauth
from custom_components.shelly_cloud_diy.api.oauth import (
    OAuthToken,
    ShellyOAuthError,
    ShellyOAuthRateLimitError,
    ShellyOAuthTransportError,
    ShellyTokenManager,
    login,
    refresh_token,
    sha1_password,
)

# ── Fake credentials. Nothing here is real, and nothing here may ever be. ──
FAKE_EMAIL = "nobody@example.invalid"
FAKE_PASSWORD = "correct-horse-battery-staple"  # noqa: S105
FAKE_ACCESS = "FAKE-ACCESS-TOKEN-11111111"  # noqa: S105
FAKE_REFRESH = "FAKE-REFRESH-TOKEN-22222222"  # noqa: S105
FAKE_SECOND_ACCESS = "FAKE-ACCESS-TOKEN-33333333"  # noqa: S105

ACCOUNT_HOST = "shelly-42-eu.shelly.cloud"
SERVER_URI = f"https://{ACCOUNT_HOST}"


def _jwt(claims: dict[str, Any]) -> str:
    """Build a signature-free JWT. The client reads claims, never verifies."""
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def _login_code(user_api_url: str = SERVER_URI) -> str:
    return _jwt({"user_api_url": user_api_url, "sub": "nobody"})


class _FakeResponse:
    """Enough of ``aiohttp.ClientResponse`` for :func:`oauth._post_form`."""

    def __init__(self, status: int, payload: Any = None, text: str = "") -> None:
        self.status = status
        self._payload = payload
        self._text = text or json.dumps(payload if payload is not None else {})

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def text(self) -> str:
        return self._text

    async def json(self, content_type: str | None = None) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                SimpleNamespace(real_url=f"https://{ACCOUNT_HOST}/oauth/auth"),
                (),
                status=self.status,
                message="fake failure",
            )


class _FakeSession:
    """Records every form POST and replays canned responses."""

    def __init__(self, handler) -> None:
        self._handler = handler
        self.calls: list[tuple[str, dict[str, str]]] = []

    def post(self, url: str, data=None, timeout=None):  # noqa: ANN001 - aiohttp shape
        self.calls.append((url, dict(data or {})))
        return self._handler(url, dict(data or {}))


def _standard_handler(
    *, token: str = FAKE_ACCESS, refresh: str | None = FAKE_REFRESH
):
    """A cloud that answers the verified two-step flow."""

    def handler(url: str, payload: dict[str, str]) -> _FakeResponse:
        if url.endswith("/oauth/login"):
            return _FakeResponse(200, {"isok": True, "data": {"code": _login_code()}})
        if url.endswith("/oauth/auth"):
            body: dict[str, Any] = {
                "access_token": token,
                "token_type": "Bearer",
                "expires_in": 43200,
            }
            if refresh is not None:
                body["refresh_token"] = refresh
            return _FakeResponse(200, body)
        raise AssertionError(f"unexpected URL {url}")

    return handler


# ── The sign-in flow ────────────────────────────────────────────────────────


def test_login_follows_the_account_host_from_the_code():
    """The exchange must hit the host the login code names, not the one passed in.

    The account is the authority on its own regional host; the caller's stored
    URI is only the fallback. Getting this backwards works fine on a
    single-region account and fails for everyone else.
    """
    session = _FakeSession(_standard_handler())
    token = asyncio.run(
        login(session, "https://wrong-host.invalid", FAKE_EMAIL, sha1_password(FAKE_PASSWORD))
    )

    assert token.access_token == FAKE_ACCESS
    assert token.refresh_token == FAKE_REFRESH
    assert token.expires_at > time.time()
    login_url, login_payload = session.calls[0]
    exchange_url, _ = session.calls[1]
    assert login_url == oauth.OAUTH_LOGIN_URL
    assert exchange_url == f"{SERVER_URI}/oauth/auth"
    # The plaintext password never leaves the boundary function.
    assert login_payload["password"] == sha1_password(FAKE_PASSWORD)
    assert FAKE_PASSWORD not in json.dumps(login_payload)


def test_missing_access_token_raises_instead_of_returning_none():
    """A renamed field must fail loudly at setup, not half-configure an entry."""

    def handler(url: str, payload: dict[str, str]) -> _FakeResponse:
        if url.endswith("/oauth/login"):
            return _FakeResponse(200, {"data": {"code": _login_code()}})
        return _FakeResponse(200, {"token_type": "Bearer"})

    with pytest.raises(ShellyOAuthError):
        asyncio.run(
            login(_FakeSession(handler), SERVER_URI, FAKE_EMAIL, sha1_password("x"))
        )


def test_missing_login_code_raises():
    """Same rule one step earlier: no ``code`` is a failure, not an empty string."""

    def handler(url: str, payload: dict[str, str]) -> _FakeResponse:
        return _FakeResponse(200, {"isok": True, "data": {}})

    with pytest.raises(ShellyOAuthError):
        asyncio.run(
            login(_FakeSession(handler), SERVER_URI, FAKE_EMAIL, sha1_password("x"))
        )


def test_rejected_credentials_raise_a_sanitised_error():
    """A 401 carries a status code — never the server body or the payload."""

    def handler(url: str, payload: dict[str, str]) -> _FakeResponse:
        return _FakeResponse(
            401, {"isok": False, "errors": {"invalid_credentials": FAKE_ACCESS}}
        )

    with pytest.raises(ShellyOAuthError) as excinfo:
        asyncio.run(
            oauth.request_code(_FakeSession(handler), FAKE_EMAIL, sha1_password(FAKE_PASSWORD))
        )
    message = str(excinfo.value)
    assert "401" in message
    assert FAKE_ACCESS not in message
    assert sha1_password(FAKE_PASSWORD) not in message


def test_rate_limit_is_not_mistaken_for_bad_credentials(monkeypatch):
    """Shelly reports its 1 req/s limit as HTTP 401 with a ``max_req`` body.

    Only the body separates it from a rejected credential, and mistaking one
    for the other would push a working account into a re-authentication prompt.
    """
    monkeypatch.setattr(oauth.asyncio, "sleep", _instant_sleep)
    body = '{"isok": false, "errors": {"max_req": "Request limit reached!"}}'

    def handler(url: str, payload: dict[str, str]) -> _FakeResponse:
        return _FakeResponse(401, json.loads(body), text=body)

    with pytest.raises(ShellyOAuthRateLimitError):
        asyncio.run(
            oauth.request_code(_FakeSession(handler), FAKE_EMAIL, sha1_password("x"))
        )


def test_transport_failure_never_quotes_the_aiohttp_error():
    """``ClientResponseError.__str__`` embeds the request URL — report the type."""

    def handler(url: str, payload: dict[str, str]) -> _FakeResponse:
        return _FakeResponse(500, {"boom": True})

    with pytest.raises(ShellyOAuthTransportError) as excinfo:
        asyncio.run(
            oauth.request_code(_FakeSession(handler), FAKE_EMAIL, sha1_password("x"))
        )
    assert "ClientResponseError" in str(excinfo.value)


async def _instant_sleep(delay: float) -> None:
    """Replacement for ``asyncio.sleep`` so a backoff costs no wall-clock time."""
    return None


# ── Refresh ─────────────────────────────────────────────────────────────────


def test_refresh_keeps_a_non_rotating_secret():
    """Measured: the refresh token does not rotate.

    A response without one is the normal case; treating it as "the secret is
    gone" would strand the account after its very first refresh.
    """
    session = _FakeSession(_standard_handler(token=FAKE_SECOND_ACCESS, refresh=None))
    token = asyncio.run(refresh_token(session, SERVER_URI, FAKE_REFRESH))

    assert token.access_token == FAKE_SECOND_ACCESS
    assert token.refresh_token == FAKE_REFRESH
    url, payload = session.calls[0]
    assert url == f"{SERVER_URI}/oauth/auth"
    assert payload["grant_type"] == "refresh_token"


def test_refresh_prefers_a_rotated_secret_when_one_arrives():
    """If the server ever starts rotating, the new secret wins."""
    rotated = "FAKE-REFRESH-TOKEN-44444444"  # noqa: S105
    session = _FakeSession(_standard_handler(refresh=rotated))
    token = asyncio.run(refresh_token(session, SERVER_URI, FAKE_REFRESH))
    assert token.refresh_token == rotated


def test_expiry_falls_back_when_the_token_is_unreadable():
    """A malformed token yields a short TTL rather than an exception.

    The caller still holds a usable token; it simply refreshes sooner. Raising
    here would turn a cosmetic wire change into a dead integration.
    """
    session = _FakeSession(
        lambda url, payload: _FakeResponse(200, {"access_token": "not-a-jwt"})
    )
    token = asyncio.run(refresh_token(session, SERVER_URI, FAKE_REFRESH))
    assert 0 < token.expires_at - time.time() <= oauth.TOKEN_FALLBACK_TTL_S


# ── Token manager ───────────────────────────────────────────────────────────


def test_a_valid_token_is_served_without_a_round_trip():
    """The ordinary case must cost no traffic — the budget is 1 req/s."""
    session = _FakeSession(_standard_handler())
    manager = ShellyTokenManager(
        session,
        SERVER_URI,
        token=OAuthToken(FAKE_ACCESS, time.time() + 86400, FAKE_REFRESH),
    )
    assert asyncio.run(manager.async_get_token()) == FAKE_ACCESS
    assert session.calls == []


def test_a_token_near_expiry_is_refreshed_and_handed_to_the_caller():
    """Proactive refresh, so the prompt arrives before an outage, not after."""
    saved: list[OAuthToken] = []

    async def _save(token: OAuthToken) -> None:
        saved.append(token)

    manager = ShellyTokenManager(
        _FakeSession(_standard_handler(token=FAKE_SECOND_ACCESS)),
        SERVER_URI,
        token=OAuthToken(FAKE_ACCESS, time.time() + 5, FAKE_REFRESH),
        on_token_refreshed=_save,
    )
    assert asyncio.run(manager.async_get_token()) == FAKE_SECOND_ACCESS
    assert [t.access_token for t in saved] == [FAKE_SECOND_ACCESS]
    assert manager.auth_failed is False


def test_concurrent_callers_trigger_a_single_refresh():
    """Reconnect storms must not become request storms against a 1 req/s budget."""
    session = _FakeSession(_standard_handler(token=FAKE_SECOND_ACCESS))
    manager = ShellyTokenManager(
        session,
        SERVER_URI,
        token=OAuthToken(FAKE_ACCESS, time.time() - 1, FAKE_REFRESH),
    )

    async def _race() -> list[str]:
        return await asyncio.gather(*(manager.async_get_token() for _ in range(5)))

    assert asyncio.run(_race()) == [FAKE_SECOND_ACCESS] * 5
    assert len(session.calls) == 1


def test_no_refresh_token_asks_the_user_rather_than_guessing():
    """No password is stored, so a missing refresh secret genuinely needs a human."""
    manager = ShellyTokenManager(_FakeSession(_standard_handler()), SERVER_URI)
    with pytest.raises(ConfigEntryAuthFailed):
        asyncio.run(manager.async_get_token())
    assert manager.auth_failed is True


def test_a_rejected_refresh_escalates_to_reauth():
    """A refused refresh token is a credential failure, and is reported as one."""
    manager = ShellyTokenManager(
        _FakeSession(lambda url, payload: _FakeResponse(401, {"isok": False})),
        SERVER_URI,
        token=OAuthToken(FAKE_ACCESS, time.time() - 1, FAKE_REFRESH),
    )
    with pytest.raises(ConfigEntryAuthFailed):
        asyncio.run(manager.async_force_refresh())
    assert manager.auth_failed is True


def test_a_rate_limited_refresh_does_not_escalate(monkeypatch):
    """A rate limit says nothing about the credential.

    Escalating it would drag a perfectly good account through a sign-in prompt
    because the Shelly app happened to make a request in the same second.
    """
    monkeypatch.setattr(oauth.asyncio, "sleep", _instant_sleep)
    body = '{"isok": false, "errors": {"max_req": "Request limit reached!"}}'
    manager = ShellyTokenManager(
        _FakeSession(lambda url, payload: _FakeResponse(401, json.loads(body), text=body)),
        SERVER_URI,
        token=OAuthToken(FAKE_ACCESS, time.time() - 1, FAKE_REFRESH),
    )
    with pytest.raises(ShellyOAuthRateLimitError):
        asyncio.run(manager.async_get_token())
    assert manager.auth_failed is False


def test_a_failing_store_does_not_take_the_session_down():
    """Persistence is the caller's business; its failure is not the session's."""

    async def _explode(token: OAuthToken) -> None:
        raise RuntimeError("disk full")

    manager = ShellyTokenManager(
        _FakeSession(_standard_handler(token=FAKE_SECOND_ACCESS)),
        SERVER_URI,
        token=OAuthToken(FAKE_ACCESS, time.time() - 1, FAKE_REFRESH),
        on_token_refreshed=_explode,
    )
    assert asyncio.run(manager.async_get_token()) == FAKE_SECOND_ACCESS


# ── Secrecy ─────────────────────────────────────────────────────────────────


def test_a_token_cannot_be_printed():
    """The redacting ``__repr__`` is load-bearing, not decoration.

    A dataclass' generated repr puts the token into every f-string, log
    argument and traceback frame summary that touches the object.
    """
    token = OAuthToken(FAKE_ACCESS, time.time() + 60, FAKE_REFRESH)
    for rendering in (repr(token), f"{token}", str(token), str([token]), str({"t": token})):
        assert FAKE_ACCESS not in rendering
        assert FAKE_REFRESH not in rendering
    assert "expires_at" in repr(token)


def test_the_manager_cannot_be_printed():
    manager = ShellyTokenManager(
        _FakeSession(_standard_handler()),
        SERVER_URI,
        token=OAuthToken(FAKE_ACCESS, time.time() + 60, FAKE_REFRESH),
    )
    assert FAKE_ACCESS not in repr(manager)
    assert FAKE_REFRESH not in repr(manager)


def test_no_credential_reaches_a_log_record(caplog):
    """Drive the real sign-in and refresh at DEBUG, then search the output.

    Formatting each record the way ``logging`` would is the point: a leak
    hidden in a lazy ``%s`` argument is invisible to a search of the format
    strings alone.
    """
    caplog.set_level(logging.DEBUG)
    session = _FakeSession(_standard_handler())
    token = asyncio.run(
        login(session, SERVER_URI, FAKE_EMAIL, sha1_password(FAKE_PASSWORD))
    )
    manager = ShellyTokenManager(session, SERVER_URI, token=token)
    asyncio.run(manager.async_force_refresh())

    formatter = logging.Formatter("%(levelname)s %(name)s %(message)s")
    rendered = "\n".join(formatter.format(record) for record in caplog.records)
    assert rendered  # the flow must actually have logged something to search
    for secret in (
        FAKE_ACCESS,
        FAKE_REFRESH,
        FAKE_PASSWORD,
        sha1_password(FAKE_PASSWORD),
    ):
        assert secret not in rendered
