"""Authentication module for Shelly Cloud API.

Handles JWT token acquisition and refresh.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import aiohttp

from ..const import API_GET_TOKEN

if TYPE_CHECKING:
    from aiohttp import ClientSession

_LOGGER = logging.getLogger(__name__)


class ShellyAuth:
    """Manages Shelly Cloud authentication."""

    def __init__(
        self,
        session: ClientSession,
        integrator_tag: str,
        integrator_token: str,
        jwt_token: str | None = None,
    ) -> None:
        """Initialize authentication manager.

        Args:
            session: aiohttp client session
            integrator_tag: Integrator identification tag
            integrator_token: User's integrator token
            jwt_token: Optional pre-fetched JWT token
        """
        self._session = session
        self._tag = integrator_tag
        self._token = integrator_token
        self._jwt_token: str | None = jwt_token

    @property
    def jwt_token(self) -> str | None:
        """Return current JWT token."""
        return self._jwt_token

    @property
    def integrator_tag(self) -> str:
        """Return integrator tag."""
        return self._tag

    async def get_jwt_token(self) -> str:
        """Get JWT token from Shelly Cloud API.

        Returns:
            JWT token string

        Raises:
            ValueError: If API returns error
            aiohttp.ClientError: If network error
        """
        async with self._session.post(
            API_GET_TOKEN,
            data={"itg": self._tag, "token": self._token},
        ) as response:
            response.raise_for_status()
            data = await response.json()

            if not data.get("isok"):
                raise ValueError(f"Shelly API error: {data}")

            self._jwt_token = data["data"]
            _LOGGER.debug("JWT token obtained")
            return self._jwt_token

    async def refresh_token(self) -> str:
        """Refresh JWT token.

        Returns:
            New JWT token string
        """
        token = await self.get_jwt_token()
        _LOGGER.info("JWT token refreshed")
        return token

    async def validate_token(self) -> bool:
        """Validate integrator token by attempting to get JWT.

        Returns:
            True if token is valid
        """
        try:
            await self.get_jwt_token()
            return True
        except (aiohttp.ClientError, ValueError):
            return False
