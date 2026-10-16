"""FastAPI Insteon Bridge Application."""

import logging
from contextlib import asynccontextmanager
from typing import List, Optional

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager: Start watchdog on startup, clean up on shutdown."""
    logger.info("Starting Insteon Bridge Service...")
    session_manager.start_watchdog()
    yield
    logger.info("Shutting down Insteon Bridge Service...")
    # Clean up hardware lock and guarantee openHAB is re-enabled on shutdown
    if session_manager.is_active:
        logger.warning("Active session detected during shutdown; restoring openHAB...")
        await session_manager.release_session(force=True)


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
async def get_all_network_aldb():
    """Get all ALDB records across the PLM and all line devices for complete topology."""
    try:
        return await insteon_manager.get_all_network_aldb()
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
    group: int = Query(..., ge=1, le=255, description="Scene/All-Link group"),
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




