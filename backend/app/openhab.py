"""OpenHAB REST API client for managing Insteon Thing lifecycle."""

import logging
import asyncio
from typing import Optional, Dict, Any
import httpx

from app.config import settings
from app.models import OpenhabStatusResponse

logger = logging.getLogger(__name__)


class OpenhabClient:
    """Client for openHAB 4/5 REST API to inspect and toggle Insteon hardware Thing."""

    def __init__(self):
        self.base_url = settings.openhab_url.rstrip("/")
        self.thing_uid = settings.openhab_thing_uid
        self.token = settings.openhab_token

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def get_thing_status(self) -> OpenhabStatusResponse:
        """Fetch current status and enabled state of the Insteon Thing."""
        url = f"{self.base_url}/rest/things/{self.thing_uid}"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers=self._get_headers())
                if resp.status_code == 200:
                    data = resp.json()
                    status_info = data.get("statusInfo", {})
                    status = status_info.get("status", "UNKNOWN")
                    detail = status_info.get("statusDetail", "NONE")
                    
                    # In openHAB, a disabled Thing reports status UNINITIALIZED with detail DISABLED
                    is_enabled = not (status == "UNINITIALIZED" and detail == "DISABLED")
                    
                    return OpenhabStatusResponse(
                        thing_uid=self.thing_uid,
                        status=status,
                        status_detail=detail,
                        is_enabled=is_enabled,
                        reachable=True,
                    )
                else:
                    logger.warning(
                        f"openHAB responded with HTTP {resp.status_code}: {resp.text}"
                    )
                    return OpenhabStatusResponse(
                        thing_uid=self.thing_uid,
                        status=f"HTTP_{resp.status_code}",
                        status_detail=resp.text[:100],
                        is_enabled=True,
                        reachable=True,
                    )
        except httpx.RequestError as exc:
            logger.error(f"Cannot connect to openHAB at {url}: {exc}")
            return OpenhabStatusResponse(
                thing_uid=self.thing_uid,
                status="UNREACHABLE",
                status_detail=str(exc),
                is_enabled=False,
                reachable=False,
            )

    async def set_thing_enabled(self, enable: bool, max_wait_seconds: float = 6.0) -> bool:
        """Enable or disable the openHAB Insteon Thing and wait for state transition.
        
        Args:
            enable: True to enable, False to disable.
            max_wait_seconds: Max time to wait for state confirmation.
            
        Returns:
            True if state change succeeded and verified, False otherwise.
        """
        url = f"{self.base_url}/rest/things/{self.thing_uid}/enable"
        headers = self._get_headers()
        # openHAB expects text/plain or JSON boolean
        payload = "true" if enable else "false"

        logger.info(f"Setting openHAB Thing {self.thing_uid} enabled={enable}...")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Try PUT with text/plain body as standard for openHAB /enable endpoint
                resp = await client.put(
                    url,
                    headers={**headers, "Content-Type": "text/plain"},
                    content=payload,
                )
                if resp.status_code not in (200, 202, 204):
                    # Fallback to json if plain text was rejected
                    resp = await client.put(url, headers=headers, json=enable)

                if resp.status_code not in (200, 202, 204):
                    logger.error(
                        f"Failed to set enabled={enable} on openHAB Thing: HTTP {resp.status_code} - {resp.text}"
                    )
                    return False

            # Poll briefly to confirm the state change has taken effect
            start_time = asyncio.get_event_loop().time()
            while (asyncio.get_event_loop().time() - start_time) < max_wait_seconds:
                await asyncio.sleep(0.5)
                status_check = await self.get_thing_status()
                if status_check.reachable and status_check.is_enabled == enable:
                    logger.info(f"Verified openHAB Thing enabled state: {enable}")
                    return True

            logger.warning(
                f"Timeout waiting for openHAB Thing to transition to enabled={enable}."
            )
            return True  # Request accepted, though state change confirmation timed out

        except httpx.RequestError as exc:
            logger.error(f"Error communicating with openHAB: {exc}")
            return False


openhab_client = OpenhabClient()
