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

    snap_path = os.path.join("backend", "network_snapshot.json")
    if not os.path.isfile(snap_path):
        snap_path = "network_snapshot.json"
    if os.path.isfile(snap_path):
        with open(snap_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"devices": []}




def identify_links_to_restore(pristine_data: Dict[str, Any], live_snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Identify all legitimate links that are marked in_use=True in pristine but False/missing in live."""
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

            if was_in_use and not now_in_use:
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

    # 1. Update snapshot file to ensure Dining Room Spots (1A.EB.C5) and all restored slots are intact
    logger.info("Updating local network_snapshot.json with pristine records...")
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
    logger.info("Pausing openHAB Insteon Thing before hardware link restore...")
    await openhab_client.set_thing_enabled(False)
    await asyncio.sleep(2.0)

    try:
        logger.info(f"Connecting pyinsteon to {settings.insteon_port}...")
        await pyinsteon.async_connect(device=settings.insteon_port)

        # Group items by device
        by_device: Dict[str, List[Dict[str, Any]]] = {}
        for item in to_restore:
            by_device.setdefault(item["device_address"], []).append(item)

        for addr_str, items in by_device.items():
            addr_obj = Address(addr_str)
            dev = pyinsteon.devices.get(addr_obj)
            if not dev:
                logger.info(f"Identifying device {addr_str}...")
                await pyinsteon.devices.async_identify_device(addr_obj)
                dev = pyinsteon.devices.get(addr_obj)

            if not dev or not hasattr(dev, "aldb"):
                logger.warning(f"Could not initialize device {addr_str}; skipping EEPROM write.")
                continue

            logger.info(f"Restoring {len(items)} slot(s) on {addr_str} ({dev.description or 'Device'})...")
            dev.aldb.status = ALDBStatus.LOADED

            # Seed ALDB with existing known records if empty
            if len(dev.aldb) == 0 and addr_str in l_devs:
                for r in l_devs[addr_str].get("records", []):
                    try:
                        mem_int = int(r["mem_addr"], 16) if isinstance(r["mem_addr"], str) else r["mem_addr"]
                        rec = dev.aldb._create_record(
                            memory=mem_int,
                            controller=r.get("is_controller", False),
                            group=r.get("group", 1),
                            target=Address(r.get("target_address", "00.00.00")),
                            data1=r.get("data1", 0),
                            data2=r.get("data2", 0),
                            data3=r.get("data3", 0),
                            in_use=r.get("in_use", True),
                            high_water_mark=r.get("high_water_mark", False),
                            bit4=False,
                            bit5=False,
                        )
                        dev.aldb._add_record(rec)
                    except Exception:
                        pass
                dev.aldb.status = ALDBStatus.LOADED

            written_slots = 0
            for item in items:
                mem_int = int(item["mem_addr"], 16) if isinstance(item["mem_addr"], str) else item["mem_addr"]
                rec = dev.aldb.get(mem_int)
                if rec:
                    rec.in_use = True
                    rec.is_controller = item["is_controller"]
                    rec.is_responder = item["is_responder"]
                    rec.target = Address(item["target_address"])
                    rec.group = item["group"]
                    rec.data1 = item["data1"]
                    rec.data2 = item["data2"]
                    rec.data3 = item["data3"]
                else:
                    dev.aldb._create_record(
                        memory=mem_int,
                        controller=item["is_controller"],
                        group=item["group"],
                        target=Address(item["target_address"]),
                        data1=item["data1"],
                        data2=item["data2"],
                        data3=item["data3"],
                        in_use=True,
                        high_water_mark=False,
                        bit4=False,
                        bit5=False,
                    )
                written_slots += 1

            try:
                logger.info(f"Writing restored ALDB to {addr_str} EEPROM...")
                dev.aldb.status = ALDBStatus.LOADED
                dev.aldb.read_write_mode = ReadWriteMode.STANDARD
                write_success = await asyncio.wait_for(dev.aldb.async_write(force=True), timeout=25.0)
                if not write_success:
                    logger.warning(f"Standard write failed on {addr_str}; trying PEEK_POKE mode...")
                    dev.aldb.read_write_mode = ReadWriteMode.PEEK_POKE
                    dev.aldb.status = ALDBStatus.LOADED
                    write_success = await asyncio.wait_for(dev.aldb.async_write(force=True), timeout=30.0)

                if write_success:
                    logger.info(f"SUCCESS: Restored {written_slots} slot(s) on {addr_str} EEPROM.")
                else:
                    logger.error(f"FAILED writing restored slots to {addr_str} EEPROM.")
            except Exception as w_ex:
                logger.error(f"Exception writing to {addr_str}: {w_ex}")

    finally:
        logger.info("Closing pyinsteon connection...")
        try:
            await pyinsteon.async_close()
        except Exception:
            pass
        await asyncio.sleep(1.0)
        logger.info("Re-enabling openHAB Insteon Thing...")
        await openhab_client.set_thing_enabled(True)
        logger.info("Link restoration procedure complete.")


if __name__ == "__main__":
    is_dry = "--dry-run" in sys.argv
    api_arg = None
    for arg in sys.argv:
        if arg.startswith("--api-url="):
            api_arg = arg.split("=", 1)[1]
    asyncio.run(execute_link_restoration(dry_run=is_dry, api_url=api_arg))

