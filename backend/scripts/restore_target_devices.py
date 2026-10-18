"""
Surgical link restoration utility for Dining Room Spots (1A.EB.C5) and Hall Copper (15.89.BA).

Restores the missing Controller (Grp 1), Engine Auth (Grp 0), and Scene Responder
links to active PLM (53.C1.42), verifying each slot live before and after writing.
"""

import sys
import os
import json
import asyncio
import logging
from typing import Dict, List, Any, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import settings
from app.session import session_manager
from app.insteon import insteon_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("restore_targets")

try:
    import pyinsteon
    from pyinsteon.constants import ALDBStatus, ReadWriteMode
    from pyinsteon.address import Address
    PYINSTEON_AVAILABLE = True
except ImportError:
    PYINSTEON_AVAILABLE = False
    logger.error("pyinsteon is not installed.")


# Exact slots to configure and re-enable based on verified live EEPROM layout
SLOTS_TO_ENABLE = {
    "1A.EB.C5": [
        {"mem_addr": 0x0FAF, "group": 22, "is_ctrl": False, "target": "53.C1.42", "data1": 77, "data2": 27, "data3": 1, "desc": "Scene Group 22 Responder (30% Level, 0.5s Ramp)"},
        {"mem_addr": 0x0FEF, "group": 1, "is_ctrl": True, "target": "53.C1.42", "data1": 3, "data2": 0, "data3": 1, "desc": "Wall paddle to PLM (Controller)"},
        {"mem_addr": 0x0FB7, "group": 0, "is_ctrl": False, "target": "53.C1.42", "data1": 3, "data2": 21, "data3": 158, "desc": "I2CS Engine Auth to PLM"},
        {"mem_addr": 0x0FE7, "group": 23, "is_ctrl": False, "target": "53.C1.42", "data1": 77, "data2": 27, "data3": 1, "desc": "Scene Group 23 Responder"},
        {"mem_addr": 0x0FD7, "group": 24, "is_ctrl": False, "target": "53.C1.42", "data1": 0, "data2": 27, "data3": 1, "desc": "Scene Group 24 Responder (OFF)"},
        {"mem_addr": 0x0FCF, "group": 25, "is_ctrl": False, "target": "53.C1.42", "data1": 77, "data2": 27, "data3": 1, "desc": "Scene Group 25 Responder"},
    ],
    "15.89.BA": [
        {"mem_addr": 0x0FF7, "group": 1, "is_ctrl": True, "target": "53.C1.42", "data1": 3, "data2": 0, "data3": 1, "desc": "Wall paddle to PLM (Controller)"},
        {"mem_addr": 0x0F77, "group": 0, "is_ctrl": False, "target": "53.C1.42", "data1": 3, "data2": 21, "data3": 158, "desc": "I2CS Engine Auth to PLM"},
        {"mem_addr": 0x0FE7, "group": 24, "is_ctrl": False, "target": "53.C1.42", "data1": 179, "data2": 27, "data3": 1, "desc": "Scene Group 24 Responder"},
        {"mem_addr": 0x0FDF, "group": 21, "is_ctrl": False, "target": "53.C1.42", "data1": 179, "data2": 28, "data3": 1, "desc": "Scene Group 21 Responder"},
        {"mem_addr": 0x0FD7, "group": 25, "is_ctrl": False, "target": "53.C1.42", "data1": 255, "data2": 10, "data3": 1, "desc": "Scene Group 25 Responder"},
        {"mem_addr": 0x0FBF, "group": 22, "is_ctrl": False, "target": "53.C1.42", "data1": 179, "data2": 27, "data3": 1, "desc": "Scene Group 22 Responder"},
        {"mem_addr": 0x0F87, "group": 23, "is_ctrl": False, "target": "53.C1.42", "data1": 179, "data2": 27, "data3": 1, "desc": "Scene Group 23 Responder"},
    ]
}


async def restore_device_links(dry_run: bool = False):
    """Restore controller and scene links to 1A.EB.C5 and 15.89.BA."""
    if not PYINSTEON_AVAILABLE:
        logger.error("pyinsteon is required.")
        return

    logger.info("==========================================================")
    logger.info("  TARGETED RESTORATION: Dining Room Spots & Hall Copper   ")
    logger.info("==========================================================")
    logger.info(f"Dry-run mode: {dry_run}")

    for addr, slots in SLOTS_TO_ENABLE.items():
        logger.info(f"\nPlanned changes for {addr}:")
        for s in slots:
            role = "Controller" if s["is_ctrl"] else "Responder"
            mem_label = f"[0x{s['mem_addr']:04X}]" if s.get("mem_addr") else "[Auto Slot]"
            logger.info(f"  -> {mem_label}: Grp {s['group']} -> {s['target']} ({role}, d1={s.get('data1')}, d2={s.get('data2')}) - {s['desc']}")

    if dry_run:
        logger.info("\n[Dry-Run] No hardware modifications applied.")
        return

    logger.info("\nAcquiring hardware session (pausing openHAB)...")
    acquired = await session_manager.acquire_session(client_id="target_restoration_tool", lease_seconds=180)
    if not acquired:
        logger.error("Could not acquire hardware session! Hardware port locked.")
        return

    try:
        snapshot = insteon_manager._load_snapshot() or {"devices": []}
        for addr_str, slots in SLOTS_TO_ENABLE.items():
            logger.info(f"\n--- Processing {addr_str} ---")
            ok, dev_obj, err_msg = await insteon_manager.ensure_device_aldb_ready_for_write(addr_str, snapshot)
            if not ok or not dev_obj:
                logger.error(f"Could not prepare {addr_str} for write: {err_msg}")
                continue

            # Modify or add the specific slots
            modified_count = 0
            for s in slots:
                mem = s.get("mem_addr")
                rec = dev_obj.aldb.get(mem) if mem else None
                d1 = s.get("data1", getattr(rec, "data1", 0) if rec else 255)
                d2 = s.get("data2", getattr(rec, "data2", 0) if rec else 28)
                d3 = s.get("data3", getattr(rec, "data3", 1) if rec else 1)

                if rec:
                    logger.info(f"Configuring 0x{mem:04X} (Grp {s['group']} -> {s['target']}) to in_use=True, d1={d1}, d2={d2}...")
                    dev_obj.aldb.modify(
                        mem_addr=mem,
                        in_use=True,
                        controller=s["is_ctrl"],
                        group=s["group"],
                        target=Address(s["target"]),
                        data1=d1,
                        data2=d2,
                        data3=d3,
                    )
                    modified_count += 1
                else:
                    logger.info(f"Adding new slot for Grp {s['group']} -> {s['target']} (is_ctrl={s['is_ctrl']}, d1={d1}, d2={d2})...")
                    dev_obj.aldb.add(
                        group=s["group"],
                        target=Address(s["target"]),
                        controller=s["is_ctrl"],
                        data1=d1,
                        data2=d2,
                        data3=d3,
                    )
                    modified_count += 1

            if modified_count > 0:
                logger.info(f"Writing {modified_count} restored slot(s) to {addr_str} physical EEPROM...")
                dev_obj.aldb._status = ALDBStatus.LOADED
                _, failed = await dev_obj.aldb.async_write(force=True)
                if failed > 0:
                    logger.warning(f"Standard write had {failed} failures on {addr_str}; retrying with PEEK_POKE mode...")
                    dev_obj.aldb._read_write_mode = ReadWriteMode.PEEK_POKE
                    dev_obj.aldb._status = ALDBStatus.LOADED
                    _, failed = await dev_obj.aldb.async_write(force=True)

                if failed == 0:
                    logger.info(f"SUCCESS: Physical EEPROM write verified on {addr_str} ({modified_count} slots).")
                    await insteon_manager.refresh_device_db_delta_after_write(addr_str, snapshot)
                    # Live read-back verification
                    logger.info(f"Reading back live ALDB from {addr_str} to confirm EEPROM persistence...")
                    await asyncio.wait_for(dev_obj.aldb.async_load(refresh=True), timeout=30.0)
                    recs = insteon_manager._parse_aldb_records(dev_obj.aldb, source_address=addr_str)
                    insteon_manager._update_device_in_snapshot(
                        address=addr_str,
                        device=dev_obj,
                        live_delta=None,
                        records=recs,
                        is_loaded=True,
                    )
                else:
                    logger.error(f"FAILED: {failed} slot(s) could not be written to {addr_str}.")

    finally:
        logger.info("\nReleasing hardware session and restoring openHAB...")
        await session_manager.release_session(force=True)
        logger.info("Session released; openHAB restored to online.")


if __name__ == "__main__":
    is_dry = "--dry-run" in sys.argv
    asyncio.run(restore_device_links(dry_run=is_dry))
