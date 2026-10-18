"""FastAPI Insteon Bridge Application."""

import logging
import threading
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Deque, Dict, List, Optional

from fastapi import FastAPI, HTTPException, status, Query
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.models import (
    SessionStatusResponse,
    SessionAcquireRequest,
    PlmInfoResponse,
    ALDBRecordResponse,
    DeviceSummary,
    GenericResponse,
    OpenhabStatusResponse,
    SceneResponse,
    SceneMemberModel,
    SceneMemberUpdateRequest,
    SceneTriggerRequest,
    ReciprocalLinkRequest,
    DeviceReplaceRequest,
    DeviceControlRequest,
    DeviceAddRequest,
    DefaultLinkAuditResponse,
    NetworkAnalysisResponse,
)
from app.openhab import openhab_client
from app.insteon import insteon_manager
from app.session import session_manager
from app.services.scene_service import scene_service
from app.services.transaction_service import transaction_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("insteon_bridge")

# Suppress high-frequency polling from systemd journalctl
class UvicornAccessPollingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(ep in msg for ep in ("/logs", "/session/status"))

logging.getLogger("uvicorn.access").addFilter(UvicornAccessPollingFilter())
logging.getLogger("httpx").setLevel(logging.WARNING)


class BackendActivityLogHandler(logging.Handler):
    """Thread-safe ring-buffer logging handler that exposes live backend activity to the GUI log tab."""

    _IGNORED_PREFIXES = ("uvicorn", "httpx", "httpcore", "watchfiles", "asyncio")
    _SOURCE_MAP = {
        "app.insteon": "Pi.Insteon",
        "app.services.transaction_service": "Pi.Transaction",
        "insteon_tx": "Pi.Transaction",
        "app.services.scene_service": "Pi.Scenes",
        "insteon_scenes": "Pi.Scenes",
        "app.session": "Pi.Session",
        "app.openhab": "Pi.OpenHAB",
        "insteon_bridge": "Pi.Bridge",
    }

    def __init__(self, max_entries: int = 500):
        super().__init__(level=logging.INFO)
        self._entries: Deque[Dict[str, Any]] = deque(maxlen=max_entries)
        self._seq: int = 0
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            name = record.name or ""
            if any(name.startswith(prefix) for prefix in self._IGNORED_PREFIXES):
                return

            msg = record.getMessage()
            if not msg:
                return

            if name in self._SOURCE_MAP:
                source = self._SOURCE_MAP[name]
            elif name.startswith("pyinsteon"):
                source = "Pi.pyinsteon"
            else:
                source = f"Pi.{name.split('.')[-1]}"

            if record.levelno >= logging.ERROR:
                level_str = "Error"
            elif record.levelno >= logging.WARNING:
                level_str = "Warning"
            elif any(
                kw in msg
                for kw in (
                    "Complete ALDB loaded",
                    "Hardware pull complete",
                    "PLM ALDB read complete",
                    "ALDB read complete",
                    "Ping OK",
                    "Fast-skipping",
                    "Adding device to INSTEON",
                    "Session acquired",
                    "Session released",
                )
            ):
                level_str = "Success"
            else:
                level_str = "Info"

            ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]
            with self._lock:
                self._seq += 1
                self._entries.append(
                    {
                        "seq": self._seq,
                        "timestamp": ts,
                        "level": level_str,
                        "source": source,
                        "message": msg,
                    }
                )
        except Exception:
            pass

    def get_since(self, since_seq: int = 0) -> Dict[str, Any]:
        with self._lock:
            items = [e for e in self._entries if e["seq"] > since_seq]
            return {
                "latest_seq": self._seq,
                "entries": items,
            }


activity_log_handler = BackendActivityLogHandler(max_entries=500)
root_logger = logging.getLogger()
if not any(isinstance(h, BackendActivityLogHandler) for h in root_logger.handlers):
    root_logger.addHandler(activity_log_handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager: Check openHAB, start watchdog on startup, clean up on shutdown."""
    logger.info("Starting Insteon Bridge Service...")
    # Critical startup safety: If openHAB was left disabled from a previous abrupt restart, re-enable it immediately!
    try:
        oh_status = await openhab_client.get_thing_status()
        if oh_status.reachable and not oh_status.is_enabled:
            logger.warning("openHAB Insteon Thing was found DISABLED at startup. Restoring openHAB to ONLINE...")
            await openhab_client.set_thing_enabled(True)
        else:
            logger.info(f"Startup openHAB check: status={oh_status.status}, enabled={oh_status.is_enabled}")
    except Exception as exc:
        logger.warning(f"Startup openHAB check notice: {exc}")

    session_manager.start_watchdog()
    yield
    logger.info("Shutting down Insteon Bridge Service...")
    # Clean up hardware lock and guarantee openHAB is re-enabled on shutdown
    try:
        await session_manager.release_session(force=True)
        await openhab_client.set_thing_enabled(True)
    except Exception as exc:
        logger.error(f"Error restoring openHAB during shutdown: {exc}")



app = FastAPI(
    title="Insteon Network Management Bridge",
    description="Bridge API for Insteon network management with openHAB hardware arbitration",
    version="0.1.0",
    lifespan=lifespan,
)

# Enable CORS for desktop/web client communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=GenericResponse)
async def health_check():
    """System health check and connection overview."""
    oh_status = await openhab_client.get_thing_status()
    session_status = await session_manager.get_status()
    return GenericResponse(
        success=True,
        message="Insteon Bridge API is running",
        data={
            "session": session_status.model_dump(),
            "openhab": oh_status.model_dump(),
            "port": settings.insteon_port,
        },
    )


@app.get("/logs")
async def get_backend_logs(since_seq: int = Query(default=0, ge=0, description="Return log entries with seq > since_seq")):
    """Return incremental backend activity logs for real-time display in the GUI Diagnostic Log tab."""
    return activity_log_handler.get_since(since_seq=since_seq)


# --- Session Management Endpoints ---


@app.get("/session/status", response_model=SessionStatusResponse)
async def get_session_status():
    """Get the current hardware lock and session state."""
    return await session_manager.get_status()


@app.post("/session/acquire", response_model=GenericResponse)
async def acquire_session(req: SessionAcquireRequest):
    """Acquire exclusive session lock. Disables openHAB Insteon Thing and connects pyinsteon."""
    success = await session_manager.acquire_session(
        client_id=req.client_id,
        lease_seconds=req.lease_seconds,
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Could not acquire session. Hardware is locked by another client or openHAB could not be released.",
        )
    return GenericResponse(
        success=True,
        message=f"Session acquired successfully for client '{req.client_id}'",
    )


@app.post("/session/heartbeat", response_model=GenericResponse)
async def heartbeat(req: SessionAcquireRequest):
    """Send heartbeat to maintain active session lease."""
    success = await session_manager.heartbeat(client_id=req.client_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session or heartbeat rejected",
        )
    return GenericResponse(success=True, message="Lease extended")


@app.post("/session/release", response_model=GenericResponse)
async def release_session(req: SessionAcquireRequest):
    """Release session lock. Disconnects pyinsteon and restores openHAB Insteon Thing."""
    success = await session_manager.release_session(client_id=req.client_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session could not be released (mismatched client ID)",
        )
    return GenericResponse(
        success=True,
        message="Session released and openHAB Thing re-enabled",
    )


# --- openHAB Integration Endpoints ---


@app.get("/openhab/status", response_model=OpenhabStatusResponse)
async def get_openhab_status():
    """Query current status of the Insteon Thing directly from openHAB."""
    return await openhab_client.get_thing_status()


@app.post("/openhab/enable", response_model=GenericResponse)
async def enable_openhab_manually():
    """Manually force openHAB Insteon Thing back to enabled state."""
    if session_manager.is_active:
        await session_manager.release_session(force=True)
    success = await openhab_client.set_thing_enabled(True)
    return GenericResponse(
        success=success,
        message="openHAB Insteon Thing enabled" if success else "Failed to enable openHAB Thing",
    )


# --- Insteon Hardware & ALDB Endpoints ---


@app.get("/insteon/plm", response_model=PlmInfoResponse)
async def get_plm_info(force_refresh: bool = Query(False, description="Force hardware ALDB read")):
    """Get PLM modem info and its All-Link Database."""
    if force_refresh and not session_manager.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active session required before querying Insteon hardware directly. Call POST /session/acquire first.",
        )
    try:
        return await insteon_manager.get_plm_info(force_refresh_aldb=force_refresh)
    except Exception as exc:
        logger.error(f"Error querying PLM: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/insteon/plm/aldb", response_model=List[ALDBRecordResponse])
async def get_plm_aldb(force_refresh: bool = Query(False, description="Force hardware ALDB read")):
    """Get all ALDB records stored in the PLM."""
    if force_refresh and not session_manager.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active session required before querying Insteon hardware directly. Call POST /session/acquire first.",
        )
    try:
        plm_info = await insteon_manager.get_plm_info(force_refresh_aldb=force_refresh)
        return plm_info.records
    except Exception as exc:
        logger.error(f"Error querying PLM ALDB: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/insteon/devices", response_model=List[DeviceSummary])
async def get_devices():
    """List all known Insteon devices (reads cached metadata when offline)."""
    try:
        return await insteon_manager.get_devices()
    except Exception as exc:
        logger.error(f"Error querying devices: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/insteon/devices/{address}/aldb", response_model=List[ALDBRecordResponse])
async def get_device_aldb(
    address: str,
    force_refresh: bool = Query(False, description="Force live hardware ALDB read if db_delta changed or forced"),
):
    """Get or refresh a single device's ALDB (using db_delta fast-skip and I2 PEEK_POKE fallback)."""
    if force_refresh and not session_manager.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active session required before querying Insteon devices directly from hardware.",
        )
    try:
        return await insteon_manager.get_device_aldb(address=address, force_refresh=force_refresh)
    except Exception as exc:
        logger.error(f"Error querying ALDB for device {address}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/insteon/devices/{address}/control", response_model=GenericResponse)
async def control_device(address: str, req: DeviceControlRequest):
    """Trigger device action: on, off, fast_on, fast_off, dim / set_level."""
    try:
        ok = await insteon_manager.control_device(
            address=address,
            action=req.action,
            level=req.level if req.level is not None else 255,
            ramp_rate=req.ramp_rate,
            button=req.button or 1,
        )
        return GenericResponse(
            success=ok,
            message=f"Device {address} {req.action} command {'succeeded' if ok else 'failed'}."
        )
    except Exception as exc:
        logger.error(f"Error controlling device {address}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/insteon/network/aldb", response_model=List[ALDBRecordResponse])
async def get_all_network_aldb(
    force_refresh: bool = Query(default=False, description="Force live hardware crawl of PLM and peer devices"),
):
    """Get all ALDB records across the PLM and all line devices for complete topology."""
    if force_refresh and not session_manager.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active session required before querying Insteon devices directly from hardware.",
        )
    try:
        return await insteon_manager.get_all_network_aldb(force_refresh=force_refresh)
    except Exception as exc:
        logger.error(f"Error querying network ALDB: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))



# --- Scene Management Endpoints ---


@app.get("/insteon/scenes", response_model=List[SceneResponse])
async def get_all_scenes():
    """List all Insteon scenes with their controllers and member device presets."""
    try:
        return scene_service.get_all_scenes()
    except Exception as exc:
        logger.error(f"Error querying scenes: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/insteon/scenes/{group}", response_model=SceneResponse)
async def get_scene(group: int):
    """Get full details of a single scene."""
    scene = scene_service.get_scene(group=group)
    if not scene:
        raise HTTPException(status_code=404, detail=f"Scene {group} not found")
    return scene


@app.patch("/insteon/scenes/{group}/members/{address}", response_model=SceneMemberModel)
async def update_scene_member_preset(group: int, address: str, update: SceneMemberUpdateRequest):
    """Surgically adjust On-Level (brightness) or Ramp Rate for a single device in a scene."""
    try:
        return await scene_service.update_scene_member_preset(group=group, address=address, update=update)
    except ValueError as val_err:
        raise HTTPException(status_code=404, detail=str(val_err))
    except Exception as exc:
        logger.error(f"Error updating scene {group} member {address}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/insteon/scenes/{group}/trigger", response_model=GenericResponse)
async def trigger_scene(group: int, req: SceneTriggerRequest):
    """Broadcast an All-Link command (on/off) to trigger a scene live."""
    try:
        ok = await scene_service.trigger_scene(group=group, action=req.action)
        return GenericResponse(
            success=ok,
            message=f"Scene {group} triggered with action '{req.action}'" if ok else f"Failed triggering Scene {group}",
        )
    except Exception as exc:
        logger.error(f"Error triggering scene {group}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# --- Link & Device Lifecycle Endpoints ---


@app.post("/insteon/links", response_model=GenericResponse)
async def create_reciprocal_link(req: ReciprocalLinkRequest):
    """Atomically create a reciprocal link pair (Controller <-> Responder) with rollback."""
    try:
        return await transaction_service.create_reciprocal_link(req)
    except Exception as exc:
        logger.error(f"Error creating link pair: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/insteon/links", response_model=GenericResponse)
async def delete_reciprocal_link(
    controller_address: str = Query(..., description="Controller address"),
    responder_address: str = Query(..., description="Responder address"),
    group: int = Query(..., ge=0, le=255, description="Scene/All-Link group"),
):
    """Atomically remove a reciprocal link pair from both devices."""
    try:
        return await transaction_service.delete_reciprocal_link(
            ctrl_addr=controller_address,
            resp_addr=responder_address,
            group=group,
        )
    except Exception as exc:
        logger.error(f"Error deleting link pair: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/insteon/devices/{address}/aldb/{mem_addr}", response_model=GenericResponse)
async def delete_device_aldb_record(address: str, mem_addr: str):
    """Mark an individual ALDB slot deleted (in_use=False) on a device."""
    try:
        return await transaction_service.delete_device_aldb_record(address=address, mem_addr=mem_addr)
    except Exception as exc:
        logger.error(f"Error deleting ALDB record {mem_addr} on {address}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/insteon/devices/replace", response_model=GenericResponse)
async def replace_device(req: DeviceReplaceRequest):
    """Execute 'Clone & Swap' replacement: clone links to new device and retarget all peers and PLM."""
    try:
        return await transaction_service.replace_device(req)
    except Exception as exc:
        logger.error(f"Error replacing device {req.old_address}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/insteon/purge-ghosts/{target_address}", response_model=GenericResponse)
async def purge_ghost_links(target_address: str):
    """Eradicate all references across the network pointing to a retired/dead device (e.g. 2C.E7.EF)."""
    try:
        return await transaction_service.purge_ghost_links(target_address=target_address)
    except Exception as exc:
        logger.error(f"Error purging ghost links for {target_address}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/insteon/audit/default-links", response_model=DefaultLinkAuditResponse)
async def audit_default_links():
    """Audit all network devices against PLM to detect missing or broken reciprocal Group 1 default links."""
    try:
        return transaction_service.audit_default_links()
    except Exception as exc:
        logger.error(f"Error auditing default links: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/insteon/devices/add", response_model=GenericResponse)
async def add_device(req: DeviceAddRequest):
    """Add a new device to the Insteon network and automatically create default reciprocal links with the PLM."""
    try:
        return await transaction_service.add_device(req)
    except Exception as exc:
        logger.error(f"Error adding device {req.address}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/insteon/network/analyze", response_model=NetworkAnalysisResponse)
async def analyze_network():
    """Deep-dive diagnostic analysis of the Insteon network to detect half-links, loopbacks, and corruption."""
    try:
        return transaction_service.analyze_network()
    except Exception as exc:
        logger.error(f"Error analyzing network: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/insteon/network/restore-pristine", response_model=GenericResponse)
async def restore_pristine_network(
    dry_run: bool = Query(False, description="Dry-run simulation only"),
    all_devices: bool = Query(False, description="Restore across all powered devices"),
):
    """Surgically restore legitimate links to active PLM (53.C1.42) and peers, while keeping ghosts purged."""
    try:
        from scripts.restore_pristine_links import execute_link_restoration
        await execute_link_restoration(dry_run=dry_run, all_devices=all_devices)
        return GenericResponse(
            success=True,
            message="Pristine link restoration executed successfully. Legitimate links restored."
        )
    except Exception as exc:
        logger.error(f"Error in restore_pristine_network: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))





