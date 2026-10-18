"""Session and Lock Manager ensuring safe arbitration between openHAB and pyinsteon."""

import time
import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional

from app.config import settings
from app.openhab import openhab_client
from app.insteon import insteon_manager
from app.models import SessionStatusResponse

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages serial port access lease, openHAB disable/enable toggle, and watchdog auto-release."""

    def __init__(self):
        self.is_active: bool = False
        self.locked_by: Optional[str] = None
        self.lease_expires_at: Optional[float] = None
        self.last_heartbeat_time: Optional[float] = None
        self._lock = asyncio.Lock()
        self._watchdog_task: Optional[asyncio.Task] = None

    def start_watchdog(self):
        """Start the background lease watchdog if not already running."""
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())
            logger.info("Session watchdog task initialized.")

    async def _watchdog_loop(self):
        """Periodically check for expired sessions and restore openHAB."""
        last_self_heal_check = 0.0
        while True:
            try:
                await asyncio.sleep(2.5)
                now = time.time()
                if self.is_active and self.lease_expires_at:
                    if now > self.lease_expires_at:
                        logger.warning(
                            f"Session lease for '{self.locked_by}' expired! "
                            f"Auto-releasing serial port and re-enabling openHAB."
                        )
                        await self.release_session(force=True)
                elif not self.is_active and not insteon_manager.is_connected:
                    # Self-healing safety net: every 15s, ensure openHAB is not accidentally left disabled
                    if (now - last_self_heal_check) > 15.0:
                        last_self_heal_check = now
                        try:
                            oh_stat = await openhab_client.get_thing_status()
                            if oh_stat.reachable and not oh_stat.is_enabled:
                                logger.warning(
                                    "Watchdog detected openHAB Insteon Thing in DISABLED state while bridge is idle! "
                                    "Auto-restoring openHAB to ENABLED..."
                                )
                                await openhab_client.set_thing_enabled(True)
                        except Exception as heal_ex:
                            logger.debug(f"Watchdog openHAB idle check note: {heal_ex}")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Error in session watchdog loop: {exc}", exc_info=True)


    async def acquire_session(self, client_id: str, lease_seconds: Optional[int] = None) -> bool:
        """Acquire lock: Disables openHAB Insteon Thing, then connects pyinsteon."""
        async with self._lock:
            duration = lease_seconds or settings.session_timeout_seconds
            now = time.time()

            # If already active, transfer/renew lease for the requesting GUI client
            if self.is_active:
                if self.locked_by != client_id:
                    logger.info(
                        f"Transferring active management session from '{self.locked_by}' to '{client_id}'."
                    )
                    self.locked_by = client_id
                self.lease_expires_at = now + duration
                self.last_heartbeat_time = now
                logger.info(f"Renewed session lease for '{client_id}'. Expires in {duration}s.")
                return True

            logger.info(f"Acquiring management session for '{client_id}'...")

            # Step 1: Disable openHAB Insteon Thing to release serial port
            logger.info("Step 1/2: Disabling openHAB Insteon Thing...")
            oh_disabled = await openhab_client.set_thing_enabled(False)
            if not oh_disabled:
                # Check if openHAB is unreachable (e.g. running in local development without openHAB)
                status = await openhab_client.get_thing_status()
                if not status.reachable:
                    logger.warning("openHAB is unreachable; proceeding with session in offline/mock test mode.")
                else:
                    logger.error("Failed to disable openHAB Insteon Thing. Aborting session acquisition.")
                    await openhab_client.set_thing_enabled(True)
                    return False

            # Allow OS serial driver 1 second to release handles on real hardware
            if not settings.is_simulator:
                await asyncio.sleep(1.0)

            # Step 2: Connect pyinsteon to hardware port
            logger.info("Step 2/2: Connecting pyinsteon to serial port...")
            try:
                connected = await insteon_manager.connect()
                if not connected:
                    raise RuntimeError("pyinsteon failed to connect.")
            except Exception as exc:
                logger.error(f"Failed to connect pyinsteon: {exc}. Rolling back openHAB...")
                await openhab_client.set_thing_enabled(True)
                return False

            # Lock acquired successfully
            self.is_active = True
            self.locked_by = client_id
            self.lease_expires_at = now + duration
            self.last_heartbeat_time = now
            logger.info(
                f"Management session successfully established for '{client_id}'. "
                f"Watchdog lease: {duration}s."
            )
            return True

    async def heartbeat(self, client_id: str) -> bool:
        """Extend lease for active session."""
        async with self._lock:
            if not self.is_active:
                logger.warning(f"Heartbeat rejected: Session not active for client '{client_id}'.")
                return False

            self.locked_by = client_id
            self.lease_expires_at = time.time() + settings.session_timeout_seconds
            self.last_heartbeat_time = time.time()
            return True

    async def release_session(self, client_id: Optional[str] = None, force: bool = False) -> bool:
        """Release lock: Disconnects pyinsteon, then re-enables openHAB Insteon Thing."""
        async with self._lock:
            if not self.is_active:
                logger.info("No active session to release.")
                return True

            logger.info(f"Releasing management session for '{self.locked_by}' (requested by '{client_id}')...")
            await self._do_release_hardware()
            return True

    async def _do_release_hardware(self):
        """Internal helper to disconnect pyinsteon and re-enable openHAB."""
        # Step 1: Disconnect pyinsteon
        logger.info("Step 1/2: Disconnecting pyinsteon...")
        try:
            await insteon_manager.disconnect()
        except Exception as exc:
            logger.error(f"Error disconnecting pyinsteon during release: {exc}")

        # Allow OS serial driver 0.5s to close port on real hardware
        if not settings.is_simulator:
            await asyncio.sleep(0.5)

        # Step 2: Re-enable openHAB Insteon Thing
        logger.info("Step 2/2: Re-enabling openHAB Insteon Thing...")
        try:
            await openhab_client.set_thing_enabled(True)
        except Exception as exc:
            logger.error(f"Error re-enabling openHAB Insteon Thing: {exc}")

        self.is_active = False
        self.locked_by = None
        self.lease_expires_at = None
        self.last_heartbeat_time = None
        logger.info("Hardware released and openHAB restored.")

    async def get_status(self) -> SessionStatusResponse:
        """Return combined status of session, pyinsteon, and openHAB."""
        remaining_seconds = None
        if self.is_active and self.lease_expires_at:
            remaining_seconds = max(0, int(self.lease_expires_at - time.time()))

        oh_status = await openhab_client.get_thing_status()

        last_hb_str = None
        if self.last_heartbeat_time:
            last_hb_str = datetime.fromtimestamp(self.last_heartbeat_time, tz=timezone.utc).isoformat()

        return SessionStatusResponse(
            is_active=self.is_active,
            locked_by=self.locked_by,
            lease_expires_in_seconds=remaining_seconds,
            openhab_thing_enabled=oh_status.is_enabled,
            pyinsteon_connected=insteon_manager.is_connected,
            last_heartbeat=last_hb_str,
        )


session_manager = SessionManager()
