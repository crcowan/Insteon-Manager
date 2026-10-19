#!/usr/bin/env python3
"""
Non-destructive, Read-Only Insteon Device Diagnostic Tool.

Safely acquires the hardware session (pausing openHAB), verifies reachability,
determines true Engine Version (0x0D), and live-reads the All-Link Database
without modifying or writing to any device EEPROM.
"""

import sys
import os
import asyncio
import logging
from typing import List, Dict, Any, Optional

# Ensure backend package is on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import settings
from app.session import session_manager
from app.insteon import insteon_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("diagnose")

try:
    import pyinsteon
    from pyinsteon.address import Address
    from pyinsteon.constants import ResponseStatus, EngineVersion, ALDBStatus
    from pyinsteon.handlers.to_device.engine_version_request import EngineVersionRequest
    from pyinsteon.commands import StatusRequestCommand
    PYINSTEON_AVAILABLE = True
except ImportError:
    PYINSTEON_AVAILABLE = False
    logger.error("pyinsteon is not installed.")


async def query_engine_version(addr_obj: Address, timeout: float = 6.0) -> tuple[bool, Optional[str]]:
    """Query true Insteon Engine Version using 0x0D Direct Command."""
    version_holder: List[Optional[Any]] = [None]

    def _on_version(engine_version):
        version_holder[0] = engine_version

    cmd = EngineVersionRequest(addr_obj)
    cmd.subscribe(_on_version)
    try:
        res = await asyncio.wait_for(cmd.async_send(), timeout=timeout)
        if res == ResponseStatus.SUCCESS and version_holder[0] is not None:
            v = version_holder[0]
            name = getattr(v, "name", str(v))
            return True, name
        return False, f"ResponseStatus: {res}"
    except asyncio.TimeoutError:
        return False, "Timeout (no response to 0x0D)"
    except Exception as ex:
        return False, str(ex)
    finally:
        try:
            cmd.unsubscribe(_on_version)
        except Exception:
            pass


async def query_status_and_delta(addr_obj: Address, timeout: float = 5.0) -> tuple[bool, Optional[int], Optional[int]]:
    """Query current on-level and db_delta using 0x19 StatusRequest."""
    delta_holder: List[Optional[int]] = [None]
    level_holder: List[Optional[int]] = [None]

    def _on_ack(db_version: int, status: int):
        delta_holder[0] = int(db_version)
        level_holder[0] = int(status)

    cmd = StatusRequestCommand(addr_obj, status_type=0)
    cmd.subscribe(_on_ack)
    try:
        res = await asyncio.wait_for(cmd.async_send(), timeout=timeout)
        if res == ResponseStatus.SUCCESS or delta_holder[0] is not None:
            return True, delta_holder[0], level_holder[0]
        return False, None, None
    except Exception:
        return False, None, None
    finally:
        try:
            cmd.unsubscribe(_on_ack)
        except Exception:
            pass


async def diagnose_device(address_str: str) -> Dict[str, Any]:
    """Interrogate a single device read-only and return its full live status."""
    clean_addr = str(Address(address_str)).upper()
    addr_obj = Address(clean_addr)
    result = {
        "address": clean_addr,
        "reachable": False,
        "db_delta": None,
        "on_level": None,
        "engine_version": None,
        "aldb_read_success": False,
        "aldb_status": "NOT_ATTEMPTED",
        "records": [],
        "plm_links": [],
        "errors": [],
    }

    logger.info(f"--- Interrogating {clean_addr} ---")

    # 1. Reachability & Delta Ping (0x19)
    ping_ok, db_delta, on_level = await query_status_and_delta(addr_obj)
    result["reachable"] = ping_ok
    result["db_delta"] = db_delta
    result["on_level"] = on_level

    if ping_ok:
        logger.info(f"[{clean_addr}] Ping OK: DB Delta = {db_delta}, Level = {on_level}")
    else:
        logger.warning(f"[{clean_addr}] Ping failed (no response to 0x19 Status Request).")
        result["errors"].append("Ping 0x19 timed out or NAKed")

    # 2. Engine Version Query (0x0D)
    ver_ok, eng_ver = await query_engine_version(addr_obj)
    if ver_ok:
        result["engine_version"] = eng_ver
        logger.info(f"[{clean_addr}] Engine Version Response: {eng_ver}")
    else:
        logger.warning(f"[{clean_addr}] Engine Version query failed: {eng_ver}")
        result["errors"].append(f"Engine 0x0D failed: {eng_ver}")

    # 3. Live ALDB Load
    try:
        dev_obj = await insteon_manager._get_or_create_pyinsteon_device(addr_obj)
        if dev_obj:
            logger.info(f"[{clean_addr}] Reading live All-Link Database from hardware...")
            load_status = await asyncio.wait_for(dev_obj.aldb.async_load(refresh=True), timeout=35.0)
            result["aldb_status"] = getattr(load_status, "name", str(load_status))
            
            recs = []
            plm_addr = "53.C1.42"
            for mem, r in dev_obj.aldb.items():
                target_str = str(r.target).upper()
                rec_dict = {
                    "mem_addr": f"0x{mem:04X}",
                    "in_use": bool(r.is_in_use),
                    "is_controller": bool(r.is_controller),
                    "is_responder": bool(not r.is_controller),
                    "high_water_mark": bool(r.is_high_water_mark),
                    "group": int(r.group),
                    "target": target_str,
                    "data1": int(r.data1),
                    "data2": int(r.data2),
                    "data3": int(r.data3),
                }
                recs.append(rec_dict)
                if r.is_in_use and target_str == plm_addr:
                    role = "Controller" if r.is_controller else "Responder"
                    result["plm_links"].append(f"Grp {r.group} ({role}) at 0x{mem:04X}")

            result["records"] = recs
            result["aldb_read_success"] = len(recs) > 0
            logger.info(f"[{clean_addr}] ALDB Read complete: {len(recs)} total slots read, {len(result['plm_links'])} active PLM link(s) found.")
    except asyncio.TimeoutError:
        logger.error(f"[{clean_addr}] ALDB Read timed out after 35s.")
        result["errors"].append("ALDB read timed out")
    except Exception as ex:
        logger.error(f"[{clean_addr}] ALDB Read error: {ex}")
        result["errors"].append(f"ALDB exception: {ex}")

    return result


async def run_diagnostics(device_addresses: List[str]):
    """Execute read-only diagnosis across target devices."""
    if not PYINSTEON_AVAILABLE:
        logger.error("pyinsteon is required to communicate with hardware.")
        return

    logger.info("==================================================")
    logger.info("   READ-ONLY INSTEON HARDWARE DIAGNOSTIC TOOL     ")
    logger.info("==================================================")
    logger.info(f"Target Devices: {', '.join(device_addresses)}")
    was_already_active = session_manager.is_active
    prev_locked_by = session_manager.locked_by
    if not was_already_active:
        logger.info("Acquiring hardware session (pausing openHAB)...")
        acquired = await session_manager.acquire_session(client_id="diagnostic_reader", lease_seconds=120)
        if not acquired:
            logger.error("Could not acquire hardware session! Port may be locked.")
            return
    else:
        logger.info(f"Using active management session (held by '{prev_locked_by}'). openHAB remains paused.")

    reports = []
    try:
        for addr in device_addresses:
            rep = await diagnose_device(addr)
            reports.append(rep)
            await asyncio.sleep(0.5)
    finally:
        if not was_already_active:
            logger.info("Releasing hardware session and restoring openHAB...")
            await session_manager.release_session(force=True)
            logger.info("Hardware released; openHAB restored.")
        else:
            logger.info(f"Diagnostics complete. Preserving active management session for '{prev_locked_by}'.")

    # Print Summary Report
    print("\n" + "=" * 60)
    print("                DIAGNOSTIC SUMMARY REPORT                 ")
    print("=" * 60)
    for r in reports:
        addr = r["address"]
        reach = "ONLINE" if r["reachable"] else "UNREACHABLE"
        ver = r["engine_version"] or "UNKNOWN"
        delta = f"0x{r['db_delta']:02X}" if r["db_delta"] is not None else "None"
        aldb_ok = "YES" if r["aldb_read_success"] else "NO"
        
        print(f"\nDevice: {addr}")
        print(f"  Reachability:     {reach}")
        print(f"  Engine Version:   {ver}")
        print(f"  DB Delta:         {delta}")
        print(f"  ALDB Readable:    {aldb_ok} (Status: {r['aldb_status']})")
        print(f"  Active Records:   {len([x for x in r['records'] if x['in_use']])}")
        print(f"  PLM Links (53.C1.42):")
        if r["plm_links"]:
            for pl in r["plm_links"]:
                print(f"    -> {pl}")
        else:
            print("    -> NONE (No active links to PLM found!)")

        if r["errors"]:
            print(f"  Errors Encountered:")
            for e in r["errors"]:
                print(f"    ! {e}")

    print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    targets = ["1A.EB.C5", "15.89.BA"]
    for arg in sys.argv[1:]:
        if arg.startswith("--devices="):
            targets = [a.strip() for a in arg.split("=", 1)[1].split(",") if a.strip()]
    asyncio.run(run_diagnostics(targets))
