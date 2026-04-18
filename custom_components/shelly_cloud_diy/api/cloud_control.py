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
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import aiohttp

if TYPE_CHECKING:
    from aiohttp import ClientSession

_LOGGER = logging.getLogger(__name__)

# Conservative default; callers typically override per-request.
_DEFAULT_TIMEOUT_S = 10


class ShellyCloudError(Exception):
    """Base class for all Cloud-Control-API errors raised by this client."""


class ShellyCloudAuthError(ShellyCloudError):
    """The auth_key or server URI was rejected by Shelly Cloud."""


class ShellyCloudRateLimitError(ShellyCloudError):
    """The 1 req/s rate limit was exceeded (HTTP 429)."""


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

    async def _post(self, path: str, extra: dict[str, Any] | None = None) -> dict:
        """POST a form request and return the parsed JSON body.

        Retries once on HTTP 429 after a 1.2 s sleep so a parallel consumer
        of the auth_key (e.g. the Shelly mobile app) briefly sharing the
        1 req/s budget does not stall the coordinator. Any further 429
        surfaces as :class:`ShellyCloudRateLimitError` so the caller can
        back off properly.
        """
        url = f"{self._base_url}{path}"
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
                        raise ShellyCloudAuthError(
                            f"Shelly Cloud rejected auth_key ({response.status})"
                        )
                    if response.status == 429:
                        if attempt == 0:
                            await asyncio.sleep(1.2)
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
            if errors and "invalid_auth_key" in str(errors).lower():
                raise ShellyCloudAuthError(f"Auth rejected: {errors}")
            raise ShellyCloudError(f"Shelly Cloud API error on {path}: {errors or data}")

        return data

    async def _post_json(self, path: str, payload: dict[str, Any]) -> Any:
        """POST a JSON request and return the parsed JSON body.

        Used by v2 endpoints, which take a JSON body (including ``auth_key``)
        and return a JSON array or object directly — not the v1
        ``{"isok": …, "data": …}`` envelope.

        Retries once on HTTP 429 after a 1.2 s sleep (same pattern as
        :meth:`_post`); any further 429 surfaces as
        :class:`ShellyCloudRateLimitError`.
        """
        url = f"{self._base_url}{path}"

        data: Any = None
        for attempt in range(2):
            try:
                async with self._session.post(
                    url, json=payload, timeout=self._timeout
                ) as response:
                    if response.status in (401, 403):
                        raise ShellyCloudAuthError(
                            f"Shelly Cloud rejected auth_key ({response.status})"
                        )
                    if response.status == 429:
                        if attempt == 0:
                            await asyncio.sleep(1.2)
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

    # Shelly's v2 API rejects requests with more than 10 ids and returns
    # HTTP 200 ``{"isok":false,"error":"VALIDATION_ERRORS",…}``. Undocumented
    # but verified live. We batch accordingly.
    _V2_IDS_PER_BATCH = 10

    # Pause between consecutive v2 requests so the 1 req/s account budget
    # holds across batches.
    _V2_BATCH_GAP_S = 1.2

    async def get_device_names(self, ids: list[str]) -> dict[str, str]:
        """Look up user-set device names via the v2 JSON API.

        POSTs ``/v2/devices/api/get`` with ``select=["settings"]`` and
        ``pick={"settings": ["sys"]}`` so the response only contains
        ``settings.sys`` for Gen2/Gen3 (the path `settings.sys.device.name`
        exposes the user-set name). The auth_key is sent in the JSON body,
        NOT as a Bearer header.

        Gen2/Gen3 Shelly devices get a name; BLE gateway-bridged entries
        (id prefix ``XB``) are skipped — they have no user-set Shelly-App
        name and would only waste API budget.

        The v2 API rejects >10 ids per request with a VALIDATION_ERRORS
        payload, so large fleets are split into batches of 10 with a
        1.2 s gap between calls to respect the shared 1 req/s budget.

        Offline devices return no settings payload and are silently
        omitted — their name stays unresolved until they come back online.
        """
        if not ids:
            return {}

        # BLE-gateway-bridged devices (Shelly BLU family, virtual XB-prefix
        # ids) don't have a Shelly-App name under settings.sys.device.name;
        # skip them so we don't waste batches.
        gen2_ids = [d for d in ids if isinstance(d, str) and not d.startswith("XB")]
        if not gen2_ids:
            return {}

        names: dict[str, str] = {}
        for batch_start in range(0, len(gen2_ids), self._V2_IDS_PER_BATCH):
            batch = gen2_ids[batch_start : batch_start + self._V2_IDS_PER_BATCH]
            if batch_start > 0:
                await asyncio.sleep(self._V2_BATCH_GAP_S)
            batch_names = await self._get_device_names_single(batch)
            names.update(batch_names)
        return names

    async def _get_device_names_single(self, ids: list[str]) -> dict[str, str]:
        """Single v2 request for up to 10 device ids. Parses + extracts names."""
        payload = {
            "auth_key": self._auth_key,
            "ids": list(ids),
            "select": ["settings"],
            "pick": {"settings": ["sys"]},
        }
        data = await self._post_json("/v2/devices/api/get", payload)

        # v2 may return either a raw list of device objects (the documented
        # success shape) or a dict envelope {"isok":…,"data":…,"error":…}
        # when validation fails or the API is rate-limited via the body
        # rather than HTTP status. Recognise the envelope and surface it
        # as a warning so future regressions don't silently drop names.
        if isinstance(data, dict):
            if data.get("isok") is False or "error" in data:
                _LOGGER.warning(
                    "v2 name lookup returned structured error: %s",
                    data.get("error") or data.get("errors") or data,
                )
                return {}
            # Older accounts have been observed to wrap the list in a dict.
            maybe_list = data.get("data") or data.get("devices")
            records = maybe_list if isinstance(maybe_list, list) else []
        elif isinstance(data, list):
            records = data
        else:
            records = []

        names: dict[str, str] = {}
        for record in records:
            if not isinstance(record, dict):
                continue
            did = record.get("id")
            if not isinstance(did, str):
                continue
            settings = record.get("settings")
            if not isinstance(settings, dict):
                continue
            # Gen2/Gen3 path: settings.sys.device.name
            name: Any = None
            sys_block = settings.get("sys")
            if isinstance(sys_block, dict):
                device_block = sys_block.get("device")
                if isinstance(device_block, dict):
                    name = device_block.get("name")
            # Gen1 fallback: settings.name (untested, documented in v2 spec)
            if not name:
                name = settings.get("name")
            if isinstance(name, str) and name.strip():
                names[did] = name.strip()

        return names

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

        body = await self._post(
            "/device/light/control",
            {
                "id": device_id,
                "channel": channel,
                "turn": turn,
                "brightness": brightness,
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
    ) -> dict[str, Any]:
        """Control a roller / cover channel.

        Args:
            direction: ``"open"``, ``"close"``, or ``"stop"``.
            go_to_pos: Target position 0..100. Mutually exclusive with
                ``direction``; Shelly accepts whichever one is present.
        """
        if direction is not None and direction not in ("open", "close", "stop"):
            raise ValueError(f"Invalid roller direction: {direction!r}")
        if go_to_pos is not None and not 0 <= go_to_pos <= 100:
            raise ValueError("go_to_pos must be 0..100")

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
