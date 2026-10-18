#!/usr/bin/env python3
"""
Surgical Insteon Network Link Restoration Utility.

Restores legitimate links to the active PLM (53.C1.42) and active peer devices
that were inadvertently marked deleted during ghost purge testing, while
strictly preserving the purged status of dead ghost devices (2C.E7.EF, 39.F1.75, 39.BA.04).
"""

import sys
import os
import json
import asyncio
import logging
from typing import Dict, List, Any, Optional, Tuple

# Ensure app package is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import settings
from app.openhab import openhab_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("restore_network")

try:
    import pyinsteon
    from pyinsteon.constants import ALDBStatus, ReadWriteMode
    from pyinsteon.address import Address
    PYINSTEON_AVAILABLE = True
except ImportError:
    PYINSTEON_AVAILABLE = False
    logger.error("pyinsteon is not installed.")


# Devices and slots that must be restored to in_use=True
PLM_ADDRESS = "53.C1.42"
DEAD_GHOST_TARGETS = {"2C.E7.EF", "39.F1.75", "39.BA.04", "34.28.58", "16.41.31", "06.52.D1"}


def load_pristine_snapshot() -> Dict[str, Any]:
    """Load pristine golden-standard snapshot."""
    paths = [
        os.path.join("backend", "network_snapshot.pristine.json"),
        "network_snapshot.pristine.json",
        os.path.join(os.path.dirname(__file__), "..", "network_snapshot.pristine.json"),
    ]
    for p in paths:
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    raise FileNotFoundError("Could not find network_snapshot.pristine.json")


def get_snapshot_path() -> str:
    """Resolve the live snapshot file path."""
    paths = [
        os.path.join("backend", "network_snapshot.json"),
        "network_snapshot.json",
        os.path.join(os.path.dirname(__file__), "..", "network_snapshot.json"),
    ]
    for p in paths:
        if os.path.isfile(p):
            return p
    return paths[0]


def load_live_data(api_url: Optional[str] = None) -> Dict[str, Any]:
    """Load live data from bridge API if available, or fall back to local snapshot file."""
    if api_url:
        import urllib.request
        try:
            url = f"{api_url.rstrip('/')}/insteon/network/aldb"
            logger.info(f"Fetching current live records from {url}...")
            req = urllib.request.urlopen(url, timeout=10)
            pi_records = json.loads(req.read().decode('utf-8'))
            by_addr: Dict[str, List[Dict[str, Any]]] = {}
            for r in pi_records:
                by_addr.setdefault(r.get("source_address", "").upper(), []).append(r)
            devices = [{"address": addr, "records": recs} for addr, recs in by_addr.items()]
            return {"devices": devices}
        except Exception as ex:
            logger.warning(f"Could not fetch from API {api_url}: {ex}; falling back to file.")

    snap_path = get_snapshot_path()
    if os.path.isfile(snap_path):
        with open(snap_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"devices": []}




AFFECTED_HARDWARE_DEVICES = {"15.89.BA", "15.88.E3", "18.9A.A2", "18.96.F5", "3C.6E.04", "3C.75.99", "1A.EB.C5"}


def identify_links_to_restore(pristine_data: Dict[str, Any], live_snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Identify all legitimate links to restore on physical hardware and snapshot."""
    p_devs = {d["address"].upper(): d for d in pristine_data.get("devices", []) if "address" in d}
    l_devs = {d["address"].upper(): d for d in live_snapshot.get("devices", []) if "address" in d}

    to_restore = []

    for addr, pd in p_devs.items():
        if addr in DEAD_GHOST_TARGETS:
            continue
        ld = l_devs.get(addr, {})
        l_recs = {r["mem_addr"]: r for r in ld.get("records", [])}

        for pr in pd.get("records", []):
            mem = pr.get("mem_addr")
            target = pr.get("target_address", "").upper()
            was_in_use = pr.get("in_use", False)

            # Never restore links pointing to dead ghost PLMs/devices!
            if target in DEAD_GHOST_TARGETS:
                continue

            # Only restore links pointing to the active PLM or active living peer devices
            if target != PLM_ADDRESS and target not in p_devs:
                continue

            lr = l_recs.get(mem)
            now_in_use = lr.get("in_use", False) if lr else False

            # Condition 1: Known affected hardware devices that underwent ghost purge testing
            # Must unconditionally verify and write all legitimate active PLM and peer links to physical EEPROM
            is_affected_link = (addr in AFFECTED_HARDWARE_DEVICES and (target == PLM_ADDRESS or target in p_devs) and was_in_use)

            # Condition 2: Any slot that was in_use in pristine but disabled in live
            is_disabled_diff = (was_in_use and not now_in_use)

            if is_affected_link or is_disabled_diff:

                to_restore.append({
                    "device_address": addr,
                    "device_label": pd.get("label", addr),
                    "mem_addr": mem,
                    "target_address": target,
                    "group": pr.get("group", 1),
                    "is_controller": pr.get("is_controller", False),
                    "is_responder": pr.get("is_responder", False),
                    "data1": pr.get("data1", 0),
                    "data2": pr.get("data2", 0),
                    "data3": pr.get("data3", 0),
                    "raw_mode": pr.get("raw_mode", ""),
                })

    return to_restore


async def execute_link_restoration(dry_run: bool = False, api_url: Optional[str] = None):
    """Safely restore all legitimate links on hardware and in the local snapshot."""
    pristine_data = load_pristine_snapshot()
    live_snapshot = load_live_data(api_url)

    to_restore = identify_links_to_restore(pristine_data, live_snapshot)
    logger.info(f"Identified {len(to_restore)} legitimate link(s) to restore across affected devices.")

    for item in to_restore:
        logger.info(
            f"  -> {item['device_address']} ({item['device_label']}) [{item['mem_addr']}]: "
            f"Grp {item['group']} -> {item['target_address']} ({item['raw_mode']})"
        )

    if dry_run:
        logger.info("Dry-run mode: No hardware or snapshot changes applied.")
        return

    # If api_url is provided and bridge is running, dispatch via the bridge service
    # which already owns the serial connection and openHAB arbitration
    if api_url:
        import urllib.request
        try:
            url = f"{api_url.rstrip('/')}/insteon/network/restore-pristine?dry_run=false"
            logger.info(f"Triggering restoration via running bridge service API at {url}...")
            req = urllib.request.Request(url, data=b"", method="POST")
            with urllib.request.urlopen(req, timeout=300) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                logger.info(f"Bridge service completed restoration: {res_data.get('message', 'Success')}")
                return
        except Exception as ex:
            logger.warning(f"Could not dispatch via bridge API ({ex}); proceeding with direct hardware execution...")

    # 1. Update snapshot file to ensure Dining Room Spots (1A.EB.C5) and all restored slots are intact
    snap_path = get_snapshot_path()
    logger.info(f"Updating local {snap_path} with pristine records...")
    p_devs = {d["address"].upper(): d for d in pristine_data.get("devices", []) if "address" in d}
    l_devs = {d["address"].upper(): d for d in live_snapshot.get("devices", []) if "address" in d}

    # Always ensure 1A.EB.C5 is present with its 24 pristine records
    if "1A.EB.C5" in p_devs:
        p_spots = p_devs["1A.EB.C5"]
        if "1A.EB.C5" not in l_devs or len(l_devs["1A.EB.C5"].get("records", [])) == 0:
            logger.info("Restoring 1A.EB.C5 (Dining Room Spots) complete profile in snapshot...")
            l_devs["1A.EB.C5"] = p_spots
        else:
            # Restore slots in 1A.EB.C5
            l_devs["1A.EB.C5"]["records"] = p_spots["records"]
            l_devs["1A.EB.C5"]["status"] = "OK"

    # Mark restored slots as in_use=True in snapshot
    for item in to_restore:
        addr = item["device_address"]
        mem = item["mem_addr"]
        if addr in l_devs:
            for r in l_devs[addr].get("records", []):
                if r.get("mem_addr") == mem:
                    r["in_use"] = True
                    break

    live_snapshot["devices"] = list(l_devs.values())
    with open(snap_path, "w", encoding="utf-8") as f:
        json.dump(live_snapshot, f, indent=2)
    logger.info(f"Persisted restored snapshot to {snap_path}.")

    if not PYINSTEON_AVAILABLE:
        logger.warning("pyinsteon not available; skipping hardware EEPROM write.")
        return

    if settings.is_simulator:
        logger.info("[Simulator] Simulated hardware write complete.")
        return

    # 2. Hardware EEPROM link writes
    from app.session import session_manager
    from app.insteon import insteon_manager

    logger.info("Acquiring hardware session and pausing openHAB...")
    acquired = await session_manager.acquire_session(client_id="network_restore_utility", lease_seconds=180)
    if not acquired:
        logger.error("Could not acquire hardware session. Is openHAB or another client locking the port?")
        return

    try:
        # Group items by device
        by_device: Dict[str, List[Dict[str, Any]]] = {}
        for item in to_restore:
            by_device.setdefault(item["device_address"], []).append(item)

        for addr_str, items in by_device.items():
            # Battery-powered devices (like 1C.2C.40 Mini Keypad) sleep between presses.
            # Their physical EEPROMs were never modified; only snapshot needs restoring.
            is_batt = any(d.get("is_battery", False) for d in pristine_data.get("devices", []) if d.get("address", "").upper() == addr_str)
            if is_batt or addr_str == "1C.2C.40":
                logger.info(f"Skipping physical RF write to battery device {addr_str} (Mini Keypad is asleep; snapshot already restored).")
                continue

            logger.info(f"Preparing {addr_str} for hardware ALDB restore ({len(items)} slots)...")
            ok, dev_obj, err_msg = await insteon_manager.ensure_device_aldb_ready_for_write(addr_str, live_snapshot)
            if not ok or not dev_obj:
                logger.warning(f"Could not prepare {addr_str} for write: {err_msg}")
                continue

            # Use pyinsteon's modify() so records are properly registered in _dirty_records
            modified_count = 0
            for item in items:
                mem_int = int(item["mem_addr"], 16) if isinstance(item["mem_addr"], str) else item["mem_addr"]
                tgt_addr = Address(item["target_address"])
                rec = dev_obj.aldb.get(mem_int)
                if rec:
                    dev_obj.aldb.modify(
                        mem_addr=mem_int,
                        in_use=True,
                        controller=item["is_controller"],
                        group=item["group"],
                        target=tgt_addr,
                        data1=item["data1"],
                        data2=item["data2"],
                        data3=item["data3"],
                    )
                    modified_count += 1
                else:
                    # Slot was not present in loaded dictionary; create and add
                    new_rec = dev_obj.aldb._create_record(
                        memory=mem_int,
                        controller=item["is_controller"],
                        group=item["group"],
                        target=tgt_addr,
                        data1=item["data1"],
                        data2=item["data2"],
                        data3=item["data3"],
                        in_use=True,
                        high_water_mark=False,
                        bit4=False,
                        bit5=False,
                    )
                    dev_obj.aldb._add_record(new_rec)
                    dev_obj.aldb._add_dirty_record(new_rec)
                    modified_count += 1

            if modified_count > 0:
                logger.info(f"Writing {modified_count} restored slot(s) to {addr_str} EEPROM...")
                dev_obj.aldb._status = ALDBStatus.LOADED
                _, failed = await dev_obj.aldb.async_write(force=True)
                if failed > 0:
                    logger.warning(f"Standard write had {failed} failures on {addr_str}; retrying with PEEK_POKE mode...")
                    dev_obj.aldb._read_write_mode = ReadWriteMode.PEEK_POKE
                    dev_obj.aldb._status = ALDBStatus.LOADED
                    _, failed = await dev_obj.aldb.async_write(force=True)

                if failed == 0:
                    logger.info(f"SUCCESS: Restored {modified_count} slot(s) on {addr_str} EEPROM.")
                    await insteon_manager.refresh_device_db_delta_after_write(addr_str, live_snapshot)
                else:
                    logger.error(f"FAILED: {failed} slot(s) could not be written to {addr_str} EEPROM.")

    finally:
        logger.info("Releasing hardware session and restoring openHAB...")
        await session_manager.release_session(force=True)
        logger.info("Link restoration procedure complete.")



if __name__ == "__main__":
    is_dry = "--dry-run" in sys.argv
    api_arg = None
    for arg in sys.argv:
        if arg.startswith("--api-url="):
            api_arg = arg.split("=", 1)[1]
    asyncio.run(execute_link_restoration(dry_run=is_dry, api_url=api_arg))

