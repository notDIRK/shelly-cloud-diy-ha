"""Shelly Cloud Control API HTTP client.

Thin async wrapper around the documented Cloud Control API endpoints:

- ``POST /device/all_status``      — snapshot of every device on the account
- ``POST /device/status``          — snapshot of a single device
- ``POST /device/relay/control``   — turn relay channels on/off/toggle
- ``POST /device/light/control``   — turn / dim light channels
- ``POST /device/relay/roller/control`` — cover / roller open/close/stop/to_pos
- ``POST /v2/devices/api/get``     — v2 JSON endpoint for device metadata
  (settings, names); auth_key goes in the JSON body, NOT as Bearer header.

All v1 calls authenticate via the form parameter ``auth_key`` (obtained from
the Shelly App under *User settings → Authorization cloud key*). The v2 call
takes the same ``auth_key`` but as a JSON body field. The per-account server
URI is passed into the client at construction time (also shown on the same
screen in the app).

Shelly documents a rate limit of **1 request per second per account**; callers
are responsible for respecting that budget. v1 and v2 share that budget. See
``docs/ROADMAP.md`` for the integration's overall rate-limit strategy.

Credential handling — canonical statement
-----------------------------------------
**This module is the only place in the integration that touches the user's
auth_key.** That is a deliberate property, not an accident: it makes the claim
"here is everywhere your credential goes" checkable with a single ``grep``
rather than a matter of trust. ``docs/AUTH_KEY.md`` is written for users and
points here; if you change anything in this section, change that file too.

Rules this module keeps, each one user-visible:

1. **One destination.** Every request goes to ``self._base_url``, built once in
   ``_normalise_base_url`` from the server URI the user entered at setup. There
   is no hard-coded fallback host and no second destination for the key.
2. **No implicit attachment.** The helpers (``_post`` / ``_post_json``) do not
   silently add credentials for their callers. ``_post`` attaches the key
   because every v1 endpoint requires it; ``_post_json`` attaches nothing, so
   each v2 caller passes it explicitly and stays visible to ``grep``. Keep it
   that way — an auto-attaching helper would make the audit trail incomplete.
3. **Never logged.** No log record may carry the key or any structure holding
   it. Log *that* a key was rejected, never the value; log URLs only where the
   key is not in the query string.
4. **Never in diagnostics.** ``diagnostics.py`` deliberately does not read
   ``entry.data``. Diagnostics downloads get attached to public bug reports.

Every site that transmits the key is marked ``# CREDENTIAL:`` below, so the
three transmissions can be enumerated by reading, not by inference.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import aiohttp

if TYPE_CHECKING:
    from aiohttp import ClientSession


@dataclass(frozen=True)
class AccountInventory:
    """The alias-independent device inventory of one Shelly account.

    ``ids`` are the RAW keys of ``data.devices`` (unfolded — transport-faithful,
    so a caller can fold them itself). Unlike :meth:`ShellyCloudControl.get_device_names`,
    which omits devices that were never renamed in the app, this carries EVERY
    device id the account lists — the only trustworthy basis for deciding that a
    device is no longer in the account.
    """

    ids: frozenset[str]
    raw_count: int
    isok: bool
    well_formed: bool

_LOGGER = logging.getLogger(__name__)

# Conservative default; callers typically override per-request.
_DEFAULT_TIMEOUT_S = 10

# Backoff before retrying a rate-limited request. Shelly's 1 req/s limiter
# keeps the window open while rejected requests keep arriving; ~1.5 s reliably
# clears a single trip in testing (1.2 s was borderline). (#6)
_RATE_LIMIT_BACKOFF_S = 1.5

# Virtual-component status/config keys look like ``number:200`` / ``boolean:201``.
# Only these are kept from the v2 ``settings`` block; switch/script/sys/etc. are
# dropped so the cached config stays small. (#9)
_VIRTUAL_COMPONENT_KEY_RE = re.compile(r"^(number|enum|text|boolean):\d+$")

# Max device ids per v2 ``/devices/api/get`` request. Shelly caps the batch;
# 10 is conservative and keeps each request light. (#9)
_V2_CONFIG_BATCH = 10


class ShellyCloudError(Exception):
    """Base class for all Cloud-Control-API errors raised by this client."""


class ShellyCloudAuthError(ShellyCloudError):
    """The auth_key or server URI was rejected by Shelly Cloud."""


class ShellyCloudRateLimitError(ShellyCloudError):
    """The 1 req/s rate limit was exceeded (HTTP 429, or HTTP 401 ``max_req``)."""


class ShellyCloudTransportError(ShellyCloudError):
    """Network-level failure (DNS, TLS, timeout, connection reset, …)."""


class ShellyCloudControl:
    """HTTP client for the Shelly Cloud Control API (auth_key flavour).

    The client keeps no long-lived state of its own; it wraps the aiohttp
    session and hands back parsed JSON responses. It is safe to share one
    instance across a whole Home Assistant config entry.
    """

    def __init__(
        self,
        session: ClientSession,
        server_uri: str,
        auth_key: str,
        *,
        request_timeout_s: int = _DEFAULT_TIMEOUT_S,
    ) -> None:
        """Initialise the client.

        Args:
            session: aiohttp client session (use ``async_get_clientsession``).
            server_uri: Per-account server hostname, e.g. ``shelly-42-eu.shelly.cloud``
                (with or without ``https://`` prefix — normalised internally).
            auth_key: The ``auth_key`` string from the Shelly App.
            request_timeout_s: Per-request timeout. Default 10 s.
        """
        self._session = session
        # CREDENTIAL (stored, not sent): the only copy this integration keeps.
        # It lives on the instance and is never written anywhere else — not to
        # a log, not to disk, not into diagnostics. See the module docstring.
        self._auth_key = auth_key
        self._base_url = self._normalise_base_url(server_uri)
        self._timeout = aiohttp.ClientTimeout(total=request_timeout_s)

    @staticmethod
    def _normalise_base_url(server_uri: str) -> str:
        """Turn ``shelly-42-eu.shelly.cloud`` / ``https://…/`` into a clean base."""
        raw = server_uri.strip()
        if not raw:
            raise ValueError("server_uri must not be empty")
        if not raw.startswith(("http://", "https://")):
            raw = "https://" + raw
        return raw.rstrip("/")

    @property
    def server_uri(self) -> str:
        """Return the normalised base URL (without trailing slash)."""
        return self._base_url

    # ── Core request plumbing ───────────────────────────────────────────

    @staticmethod
    def _is_rate_limit_body(text: str) -> bool:
        """True if a response body is Shelly's rate-limit signal.

        Shelly Cloud reports rate limiting as **HTTP 401** with a body like
        ``{"isok": false, "errors": {"max_req": "Request limit reached!"}}``
        — NOT the conventional HTTP 429. Without this check a burst of calls
        (e.g. dimming a light, which also triggers a follow-up status refresh)
        is mis-reported as an auth failure. (#6)
        """
        lowered = text.lower()
        return "max_req" in lowered or "request limit" in lowered

    async def _post(self, path: str, extra: dict[str, Any] | None = None) -> dict:
        """POST a form request and return the parsed JSON body.

        Retries once on HTTP 429 — or on the HTTP 401 ``max_req`` rate-limit
        body Shelly actually uses — after a short backoff, so a parallel consumer
        of the auth_key (e.g. the Shelly mobile app) briefly sharing the
        1 req/s budget does not stall the coordinator. Any further rate-limit
        surfaces as :class:`ShellyCloudRateLimitError` so the caller can
        back off properly.
        """
        url = f"{self._base_url}{path}"
        # CREDENTIAL (sent 1/3): form field on every v1 endpoint. Destination is
        # ``self._base_url`` — i.e. the server URI the user entered, nothing
        # else. ``payload`` must never reach a log record. (docs/AUTH_KEY.md)
        payload = {"auth_key": self._auth_key}
        if extra:
            payload.update({k: str(v) for k, v in extra.items() if v is not None})

        data: dict | None = None
        for attempt in range(2):
            try:
                async with self._session.post(
                    url, data=payload, timeout=self._timeout
                ) as response:
                    if response.status == 401 or response.status == 403:
                        # Shelly returns 401 for BOTH a bad auth_key and a
                        # rate-limit hit; only the body tells them apart. (#6)
                        body_text = await response.text()
                        if self._is_rate_limit_body(body_text):
                            if attempt == 0:
                                await asyncio.sleep(_RATE_LIMIT_BACKOFF_S)
                                continue
                            raise ShellyCloudRateLimitError(
                                "Rate limit exceeded (1 req/s)"
                            )
                        raise ShellyCloudAuthError(
                            f"Shelly Cloud rejected auth_key ({response.status})"
                        )
                    if response.status == 429:
                        if attempt == 0:
                            await asyncio.sleep(_RATE_LIMIT_BACKOFF_S)
                            continue
                        raise ShellyCloudRateLimitError(
                            "Rate limit exceeded (1 req/s)"
                        )
                    response.raise_for_status()
                    data = await response.json(content_type=None)
                    break
            except asyncio.TimeoutError as err:
                raise ShellyCloudTransportError(f"Timeout calling {path}") from err
            except aiohttp.ClientError as err:
                raise ShellyCloudTransportError(f"HTTP error calling {path}: {err}") from err

        # Shelly wraps every response in {"isok": bool, "data": …, "errors": …}
        if not isinstance(data, dict):
            raise ShellyCloudError(f"Unexpected response shape from {path}: {type(data)}")
        if data.get("isok") is False:
            # Shelly returned a structured error. Common causes: invalid auth_key
            # (isok=false + errors field) vs. unknown device (isok=false + data=null).
            errors = data.get("errors")
            # NOT a credential site: this matches Shelly's error *text*, it
            # never touches the key itself. Called out because the audit grep
            # in docs/AUTH_KEY.md returns this line too, and a reader should
            # not have to wonder about it. The raised message carries
            # ``errors`` (Shelly's own wording), never the credential.
            if errors and "invalid_auth_key" in str(errors).lower():
                raise ShellyCloudAuthError(f"Auth rejected: {errors}")
            raise ShellyCloudError(f"Shelly Cloud API error on {path}: {errors or data}")

        return data

    async def _post_json(
        self,
        path: str,
        payload: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> Any:
        """POST a JSON request and return the parsed JSON body.

        Used by v2 endpoints, which take a JSON body and return a JSON array
        or object directly — not the v1 ``{"isok": …, "data": …}`` envelope.
        Some v2 endpoints expect ``auth_key`` in the query string rather than
        the body; pass it via ``params`` in that case.

        Retries once on HTTP 429 (or the HTTP 401 ``max_req`` body) after a
        short backoff (same pattern as
        :meth:`_post`); any further 429 surfaces as
        :class:`ShellyCloudRateLimitError`.
        """
        url = f"{self._base_url}{path}"

        data: Any = None
        for attempt in range(2):
            try:
                async with self._session.post(
                    url, json=payload, params=params, timeout=self._timeout
                ) as response:
                    if response.status in (401, 403):
                        # 401 doubles as Shelly's rate-limit signal. (#6)
                        body_text = await response.text()
                        if self._is_rate_limit_body(body_text):
                            if attempt == 0:
                                await asyncio.sleep(_RATE_LIMIT_BACKOFF_S)
                                continue
                            raise ShellyCloudRateLimitError(
                                "Rate limit exceeded (1 req/s)"
                            )
                        raise ShellyCloudAuthError(
                            f"Shelly Cloud rejected auth_key ({response.status})"
                        )
                    if response.status == 429:
                        if attempt == 0:
                            await asyncio.sleep(_RATE_LIMIT_BACKOFF_S)
                            continue
                        raise ShellyCloudRateLimitError(
                            "Rate limit exceeded (1 req/s)"
                        )
                    response.raise_for_status()
                    data = await response.json(content_type=None)
                    break
            except asyncio.TimeoutError as err:
                raise ShellyCloudTransportError(f"Timeout calling {path}") from err
            except aiohttp.ClientError as err:
                raise ShellyCloudTransportError(f"HTTP error calling {path}: {err}") from err

        return data

    # ── Status endpoints ────────────────────────────────────────────────

    async def get_all_status(self) -> dict[str, Any]:
        """Fetch status for every device visible to the account.

        Returns the ``data`` sub-object verbatim, i.e. a dict shaped like::

            {
                "devices_status": { "<device_id>": {...}, ... },
                "pending_notifications": { ... },
            }

        Each device dict contains either RPC-flavour keys (``switch:0``,
        ``light:0``, ``temperature:0``, …) for Gen2/Gen3 devices, Gen1 legacy
        keys (``relays``, ``meters``, …) for older devices, or BLE sensor keys
        (``humidity:0``, ``pressure:0``, …) for Shelly BLU / gateway-bridged
        devices. The ``_dev_info`` sub-dict always carries ``id``, ``code``,
        ``gen`` (``"G1"``, ``"G2"``, ``"GBLE"``), and ``online``.
        """
        body = await self._post("/device/all_status")
        return body.get("data", {})

    async def get_device_status(self, device_id: str) -> dict[str, Any]:
        """Fetch status for a single device.

        Returns the ``data`` sub-object. Mainly useful for on-demand refresh
        after a command; normal periodic polling goes through
        :meth:`get_all_status`.
        """
        body = await self._post("/device/status", {"id": device_id})
        return body.get("data", {})

    async def get_device_names(self, ids: list[str] | None = None) -> dict[str, str]:
        """Look up the Shelly-App cloud-alias for every device on the account.

        POSTs to the v1 ``/interface/device/list`` endpoint (form-encoded,
        same 1 req/s budget as the other v1 endpoints). The response shape
        is::

            {"isok": true, "data": {"devices": {
                "<device_id>": {"id": "<device_id>", "name": "<alias>", …},
                …
            }}}

        This returns the **cloud-side alias** that appears in the Shelly
        mobile app — which is what users actually set when they rename a
        device — rather than the device-local `settings.sys.device.name`
        that only gets written when the rename is pushed down to the
        firmware. Those two names are often different; the cloud alias is
        the correct one to show in Home Assistant.

        One request covers every device on the account — no batching, no
        per-id payloads, no BLE filtering (BLE gateway-bridged devices
        are included in the response with their own aliases).

        Args:
            ids: Optional id filter. If provided, the response is trimmed
                to those ids. Defaults to "every device on the account".

        Returns:
            ``{device_id: alias}`` for devices where a non-empty alias was
            found. Devices with no alias (never renamed in the app) are
            omitted silently.
        """
        body = await self._post("/interface/device/list")
        data = body.get("data")
        if not isinstance(data, dict):
            return {}
        devices_block = data.get("devices")
        if not isinstance(devices_block, dict):
            return {}

        wanted: set[str] | None = set(ids) if ids else None
        names: dict[str, str] = {}
        for did, record in devices_block.items():
            if not isinstance(did, str) or not isinstance(record, dict):
                continue
            if wanted is not None and did not in wanted:
                continue
            name = record.get("name")
            if isinstance(name, str) and name.strip():
                names[did] = name.strip()
        return names

    async def get_account_inventory(self) -> AccountInventory:
        """Fetch the alias-independent device inventory of the account.

        POSTs to the same v1 ``/interface/device/list`` endpoint as
        :meth:`get_device_names` (form-encoded, shares the 1 req/s budget), but
        returns EVERY device id in ``data.devices`` — renamed or not — rather
        than only the aliased subset. This is the authoritative membership set:
        a device id absent from it is genuinely no longer on the account, which
        is exactly the judgement :func:`services.orphans` must make before it
        can ever detach a device. Using the alias map instead would silently
        omit never-renamed devices and risk deleting a live one.

        The response shape is::

            {"isok": true, "data": {"devices": {
                "<device_id>": {"id": "<device_id>", "name": "<alias>", …},
                …
            }}}

        Never raises for a structurally-odd payload — it degrades to an
        ``isok`` / ``well_formed`` verdict the caller uses to decide whether the
        inventory is trustworthy enough to act on (see ``orphans_core.assess_trust``).

        Returns:
            An :class:`AccountInventory` with the RAW (unfolded) ids, their
            count, the transport ``isok`` flag, and whether the device map was
            present and non-empty.
        """
        body = await self._post("/interface/device/list")
        data = body.get("data")
        devices = data.get("devices") if isinstance(data, dict) else None
        well_formed = isinstance(devices, dict) and len(devices) > 0
        ids = (
            frozenset(k for k in devices if isinstance(k, str))
            if well_formed
            else frozenset()
        )
        return AccountInventory(
            ids=ids,
            raw_count=len(ids),
            isok=(body.get("isok") is not False),
            well_formed=well_formed,
        )

    async def get_device_configs(
        self, ids: list[str]
    ) -> dict[str, dict[str, dict]]:
        """Fetch per-virtual-component config for ``ids`` via the v2 API.

        POSTs to ``/v2/devices/api/get`` with a JSON body of
        ``{"auth_key": …, "ids": [...], "select": ["settings"]}`` — the
        auth_key rides in the BODY for this v2 endpoint, not as a Bearer
        header or query param. The response is a JSON ARRAY of
        ``{"id": <device_id>, "settings": {<component_key>: <config>, …}}``.

        For virtual components the ``settings`` entries are keyed exactly
        like the status keys (``number:200``, ``boolean:201``, …) and carry
        the config the cloud status omits: the user-set ``name``, the number
        ``meta.ui.unit``, and the enum ``options`` / ``meta.ui.titles``.

        Only virtual-component keys are kept; every other settings key
        (``switch:0``, ``script:1``, ``sys``, …) is dropped to keep the
        cached config small. ids are batched into chunks of
        ``_V2_CONFIG_BATCH`` with a pause between chunks to respect the
        shared 1 req/s budget.

        Args:
            ids: Device ids to fetch config for.

        Returns:
            ``{device_id: {component_key: config_dict}}`` for every device
            that returned at least one virtual-component config. Devices
            with no virtual-component settings are omitted.

        Raises:
            ShellyCloudAuthError: auth_key rejected.
            ShellyCloudError: transport / rate-limit / protocol failure.
        """
        result: dict[str, dict[str, dict]] = {}
        if not ids:
            return result

        for offset in range(0, len(ids), _V2_CONFIG_BATCH):
            chunk = ids[offset:offset + _V2_CONFIG_BATCH]
            if offset:
                # Space successive requests out under the 1 req/s limit.
                await asyncio.sleep(_RATE_LIMIT_BACKOFF_S)
            # CREDENTIAL (sent 2/3): JSON body field. The v2 API takes the key
            # in the body, not as a Bearer header. Passed explicitly here
            # rather than injected by ``_post_json`` so that every transmission
            # stays greppable. (docs/AUTH_KEY.md)
            payload = {
                "auth_key": self._auth_key,
                "ids": chunk,
                "select": ["settings"],
            }
            data = await self._post_json("/v2/devices/api/get", payload)
            if not isinstance(data, list):
                continue
            for record in data:
                if not isinstance(record, dict):
                    continue
                did = record.get("id")
                if not isinstance(did, str):
                    continue
                settings = record.get("settings")
                if not isinstance(settings, dict):
                    continue
                comp_configs: dict[str, dict] = {}
                for key, cfg in settings.items():
                    if (
                        isinstance(key, str)
                        and _VIRTUAL_COMPONENT_KEY_RE.match(key)
                        and isinstance(cfg, dict)
                    ):
                        comp_configs[key] = cfg
                if comp_configs:
                    result[did] = comp_configs

        return result

    # ── Command endpoints ──────────────────────────────────────────────

    async def relay_control(
        self,
        device_id: str,
        turn: str,
        *,
        channel: int = 0,
    ) -> dict[str, Any]:
        """Turn a relay channel on, off, or toggle.

        Args:
            device_id: Shelly device id.
            turn: ``"on"``, ``"off"``, or ``"toggle"``.
            channel: Relay index. Defaults to 0 (the primary channel).
        """
        if turn not in ("on", "off", "toggle"):
            raise ValueError(f"Invalid relay turn value: {turn!r}")
        body = await self._post(
            "/device/relay/control",
            {"id": device_id, "channel": channel, "turn": turn},
        )
        return body.get("data", {})

    async def light_control(
        self,
        device_id: str,
        *,
        channel: int = 0,
        turn: str | None = None,
        brightness: int | None = None,
        gain: int | None = None,
        white: int | None = None,
        temp: int | None = None,
        red: int | None = None,
        green: int | None = None,
        blue: int | None = None,
    ) -> dict[str, Any]:
        """Control a light channel.

        Only the keyword arguments that are not ``None`` are sent. At least
        one of ``turn`` / ``brightness`` / colour or white-temp parameters
        should be provided or the call is a no-op.
        """
        if turn is not None and turn not in ("on", "off", "toggle"):
            raise ValueError(f"Invalid light turn value: {turn!r}")
        if brightness is not None and not 0 <= brightness <= 100:
            raise ValueError("brightness must be 0..100")
        if gain is not None and not 0 <= gain <= 100:
            raise ValueError("gain must be 0..100")

        body = await self._post(
            "/device/light/control",
            {
                "id": device_id,
                "channel": channel,
                "turn": turn,
                "brightness": brightness,
                "gain": gain,
                "white": white,
                "temp": temp,
                "red": red,
                "green": green,
                "blue": blue,
            },
        )
        return body.get("data", {})

    async def roller_control(
        self,
        device_id: str,
        *,
        channel: int = 0,
        direction: str | None = None,
        go_to_pos: int | None = None,
        gen2: bool = False,
    ) -> dict[str, Any]:
        """Control a roller / cover channel.

        Args:
            direction: ``"open"``, ``"close"``, or ``"stop"``.
            go_to_pos: Target position 0..100. Mutually exclusive with
                ``direction``; Shelly accepts whichever one is present.
            gen2: Route through the v2 cover endpoint. The legacy Gen1 roller
                endpoint has no working per-channel selector, so on multi-cover
                Gen2 devices (e.g. Shelly Pro Dual Cover PM) it always drives
                cover 0. The v2 endpoint takes a real ``channel`` field.
        """
        if direction is not None and direction not in ("open", "close", "stop"):
            raise ValueError(f"Invalid roller direction: {direction!r}")
        if go_to_pos is not None and not 0 <= go_to_pos <= 100:
            raise ValueError("go_to_pos must be 0..100")

        if gen2:
            # v2 cover endpoint: a single ``position`` field carries either the
            # direction string or the numeric target position.
            #
            # CREDENTIAL (sent 3/3) — and the one wart, documented as such in
            # docs/AUTH_KEY.md: the key rides in the QUERY STRING here, not the
            # body. The recipient is Shelly, who issued it, and the connection
            # is HTTPS, so this is not disclosure to a third party — but URLs
            # are typically logged more liberally and kept longer than bodies,
            # on their servers.
            #
            # Measured 2026-08-11 against the live API: the endpoint accepts the
            # key in the BODY too. No key -> 401 invalid_token; key in the body,
            # bogus device id -> 400 no_permissions, i.e. authentication passed
            # and only authorisation failed. So the query string is NOT required
            # and this could move into the body.
            #
            # Not moved yet on purpose: nobody here owns cover hardware, so the
            # full command path cannot be confirmed end to end, and changing
            # working control code on inference alone to win a logging nuance on
            # someone else's servers is a bad trade. Needs one confirmation from
            # a user with a cover Shelly, then this becomes a two-line change.
            position: str | int | None = (
                direction if direction is not None else go_to_pos
            )
            data = await self._post_json(
                "/v2/devices/api/set/cover",
                {"id": device_id, "channel": channel, "position": position},
                params={"auth_key": self._auth_key},
            )
            return data if isinstance(data, dict) else {}

        body = await self._post(
            "/device/relay/roller/control",
            {
                "id": device_id,
                "channel": channel,
                "direction": direction,
                "go_to_pos": go_to_pos,
            },
        )
        return body.get("data", {})

    # ── Validation ─────────────────────────────────────────────────────

    async def validate(self) -> int:
        """Cheap connectivity + credential check.

        Hits ``/device/all_status`` once and returns the number of devices
        seen. Raises :class:`ShellyCloudAuthError` if the key is rejected,
        :class:`ShellyCloudTransportError` on network issues.
        """
        data = await self.get_all_status()
        devices = data.get("devices_status", {})
        count = len(devices) if isinstance(devices, dict) else 0
        _LOGGER.debug("Cloud Control API validate: %d devices visible", count)
        return count
