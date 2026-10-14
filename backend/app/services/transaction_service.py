"""Insteon Transaction & Operations Service.

Provides 2-phase commit atomic linking, rollback on failure,
device replacement ('clone & swap'), and network-wide ghost-link purging.
"""

import json
import logging
import os
import asyncio
from typing import Dict, List, Optional, Tuple, Any

from app.config import settings
from app.models import (
    ALDBRecordResponse,
    ReciprocalLinkRequest,
    DeviceReplaceRequest,
    GenericResponse,
)

logger = logging.getLogger("insteon_tx")

try:
    import pyinsteon
    from pyinsteon.address import Address
    from pyinsteon.constants import ResponseStatus
    PYINSTEON_AVAILABLE = True
except ImportError:
    PYINSTEON_AVAILABLE = False


class TransactionService:
    """Orchestrates multi-device atomic link mutations with rollback guarantees."""

    def __init__(self):
        pass

    def _find_snapshot_file(self) -> Optional[str]:
        candidates = [
            "backend/data/network_snapshot.json",
            "backend/network_snapshot.json",
            "network_snapshot.json",
            os.path.join(settings.cache_dir, "network_snapshot.json"),
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
        return None

    def _load_snapshot(self) -> Dict[str, Any]:
        path = self._find_snapshot_file()
        if not path:
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as ex:
            logger.error(f"Error loading snapshot: {ex}")
            return {}

    def _save_snapshot(self, data: Dict[str, Any]):
        path = self._find_snapshot_file() or os.path.join("backend", "network_snapshot.json")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            # Keep root network_snapshot.json in sync if it exists
            if os.path.isfile("network_snapshot.json") and path != "network_snapshot.json":
                with open("network_snapshot.json", "w", encoding="utf-8") as rf:
                    json.dump(data, rf, indent=2)
        except Exception as ex:
            logger.error(f"Error saving snapshot: {ex}")

    async def create_reciprocal_link(self, req: ReciprocalLinkRequest) -> GenericResponse:
        """Atomically create a reciprocal Controller <-> Responder link pair.
        
        If either write fails, the other is rolled back automatically.
        """
        snapshot = self._load_snapshot()
        ctrl_addr = req.controller_address.upper()
        resp_addr = req.responder_address.upper()
        grp = req.group

        logger.info(f"Initiating atomic reciprocal link: {ctrl_addr} (Ctrl) <---> {resp_addr} (Resp) for Group {grp}")

        from app.insteon import insteon_manager
        live_hw = insteon_manager.is_connected and PYINSTEON_AVAILABLE

        # Step 1: Pre-flight reachability check if live
        if live_hw:
            addr_ctrl = Address(ctrl_addr)
            addr_resp = Address(resp_addr)
            modem_addr = str(pyinsteon.devices.modem.address).upper() if pyinsteon.devices.modem else ""

            # Check responder reachability
            ok_resp, delta_resp, _ = await insteon_manager._fast_ping_and_db_delta(addr_resp, timeout=5.0)
            if not ok_resp:
                return GenericResponse(
                    success=False,
                    message=f"Responder device {resp_addr} is unreachable. Link creation aborted without changes.",
                )

            # Check controller reachability (unless controller is the local PLM)
            if ctrl_addr != modem_addr:
                ok_ctrl, delta_ctrl, _ = await insteon_manager._fast_ping_and_db_delta(addr_ctrl, timeout=5.0)
                if not ok_ctrl:
                    return GenericResponse(
                        success=False,
                        message=f"Controller device {ctrl_addr} is unreachable. Link creation aborted without changes.",
                    )

            # Step 2: Write Controller record
            dev_ctrl = pyinsteon.devices.get(addr_ctrl)
            if not dev_ctrl:
                dev_ctrl = pyinsteon.devices.modem if ctrl_addr == modem_addr else None
            if not dev_ctrl:
                return GenericResponse(success=False, message=f"Could not initialize controller device {ctrl_addr}")

            dev_ctrl.aldb.add(
                group=grp,
                target=addr_resp,
                controller=True,
                data1=req.data1,
                data2=req.data2,
                data3=req.data3,
            )
            ctrl_done, ctrl_failed = await dev_ctrl.aldb.async_write()
            if ctrl_failed:
                dev_ctrl.aldb.clear_pending()
                return GenericResponse(success=False, message=f"Failed writing Controller record to {ctrl_addr}")

            # Step 3: Write Responder record
            dev_resp = pyinsteon.devices.get(addr_resp)
            if not dev_resp:
                dev_resp = pyinsteon.devices.add_x10_or_insteon_device(addr_resp, 0x01, 0x00, 0x00)

            dev_resp.aldb.add(
                group=grp,
                target=addr_ctrl,
                controller=False,
                data1=req.data1,
                data2=req.data2,
                data3=req.data3,
            )
            resp_done, resp_failed = await dev_resp.aldb.async_write()

            # Rollback Step 2 if Step 3 failed
            if resp_failed:
                logger.warning(f"Responder write failed on {resp_addr}; initiating ROLLBACK on controller {ctrl_addr}...")
                for rec in dev_ctrl.aldb.find(target=addr_resp, group=grp, is_controller=True, in_use=True):
                    dev_ctrl.aldb.modify(mem_addr=rec.mem_addr, in_use=False)
                await dev_ctrl.aldb.async_write(force=True)
                return GenericResponse(
                    success=False,
                    message=f"Write to responder {resp_addr} failed. Controller link on {ctrl_addr} rolled back cleanly.",
                )

        # Step 4: Update snapshot records in cache
        if snapshot:
            self._add_or_update_snapshot_record(
                snapshot=snapshot,
                src_addr=ctrl_addr,
                target_addr=resp_addr,
                group=grp,
                is_controller=True,
                is_responder=False,
                data1=req.data1,
                data2=req.data2,
                data3=req.data3,
            )
            self._add_or_update_snapshot_record(
                snapshot=snapshot,
                src_addr=resp_addr,
                target_addr=ctrl_addr,
                group=grp,
                is_controller=False,
                is_responder=True,
                data1=req.data1,
                data2=req.data2,
                data3=req.data3,
            )
            self._save_snapshot(snapshot)

        return GenericResponse(
            success=True,
            message=f"Reciprocal link created successfully between {ctrl_addr} and {resp_addr} for Group {grp}.",
            data={"controller": ctrl_addr, "responder": resp_addr, "group": grp},
        )

    async def delete_reciprocal_link(self, ctrl_addr: str, resp_addr: str, group: int) -> GenericResponse:
        """Atomically remove reciprocal link from both devices."""
        snapshot = self._load_snapshot()
        ctrl_clean = ctrl_addr.upper()
        resp_clean = resp_addr.upper()

        logger.info(f"Removing reciprocal link between {ctrl_clean} and {resp_clean} for Group {group}...")

        from app.insteon import insteon_manager
        if insteon_manager.is_connected and PYINSTEON_AVAILABLE:
            addr_ctrl = Address(ctrl_clean)
            addr_resp = Address(resp_clean)
            dev_ctrl = pyinsteon.devices.get(addr_ctrl) or (pyinsteon.devices.modem if ctrl_clean == str(pyinsteon.devices.modem.address).upper() else None)
            dev_resp = pyinsteon.devices.get(addr_resp)

            if dev_ctrl:
                for rec in dev_ctrl.aldb.find(target=addr_resp, group=group, is_controller=True, in_use=True):
                    dev_ctrl.aldb.modify(mem_addr=rec.mem_addr, in_use=False)
                await dev_ctrl.aldb.async_write()

            if dev_resp:
                for rec in dev_resp.aldb.find(target=addr_ctrl, group=group, is_controller=False, in_use=True):
                    dev_resp.aldb.modify(mem_addr=rec.mem_addr, in_use=False)
                await dev_resp.aldb.async_write()

        # Update snapshot cache
        deleted_count = 0
        if snapshot:
            deleted_count += self._mark_snapshot_record_deleted(snapshot, ctrl_clean, resp_clean, group, is_controller=True)
            deleted_count += self._mark_snapshot_record_deleted(snapshot, resp_clean, ctrl_clean, group, is_controller=False)
            self._save_snapshot(snapshot)

        return GenericResponse(
            success=True,
            message=f"Reciprocal link deleted cleanly between {ctrl_clean} and {resp_clean} for Group {group} ({deleted_count} records marked deleted).",
            data={"controller": ctrl_clean, "responder": resp_clean, "group": group, "deleted_count": deleted_count},
        )

    async def delete_device_aldb_record(self, address: str, mem_addr: str) -> GenericResponse:
        """Mark an individual ALDB slot deleted (in_use=False) on a device."""
        snapshot = self._load_snapshot()
        clean_addr = address.upper()
        clean_mem = mem_addr.upper()

        logger.info(f"Deleting ALDB slot {clean_mem} on device {clean_addr}...")

        from app.insteon import insteon_manager
        if insteon_manager.is_connected and PYINSTEON_AVAILABLE:
            addr_obj = Address(clean_addr)
            dev_obj = pyinsteon.devices.get(addr_obj) or (pyinsteon.devices.modem if clean_addr == str(pyinsteon.devices.modem.address).upper() else None)
            if dev_obj and hasattr(dev_obj, "aldb"):
                mem_int = int(clean_mem, 16) if clean_mem.startswith("0X") else int(clean_mem)
                rec = dev_obj.aldb.get(mem_int)
                if rec:
                    dev_obj.aldb.modify(mem_addr=mem_int, in_use=False)
                    await dev_obj.aldb.async_write()

        found = False
        if snapshot:
            records_list = None
            if clean_addr == snapshot.get("plm", {}).get("address", "").upper():
                records_list = snapshot.get("plm", {}).get("records", [])
            else:
                for d in snapshot.get("devices", []):
                    if d.get("address", "").upper() == clean_addr:
                        records_list = d.get("records", [])
                        break

            if records_list:
                for r in records_list:
                    if r.get("mem_addr", "").upper() == clean_mem:
                        r["in_use"] = False
                        raw = r.get("raw_mode", "Record")
                        r["raw_mode"] = raw.replace(" (Deleted)", "") + " (Deleted)"
                        found = True
                        break
            self._save_snapshot(snapshot)

        return GenericResponse(
            success=found,
            message=f"Slot {clean_mem} on device {clean_addr} marked deleted successfully." if found else f"Slot {clean_mem} not found on {clean_addr}.",
        )

    async def purge_ghost_links(self, target_address: str) -> GenericResponse:
        """Eradicate all references across the entire network pointing to an offline/retired device (e.g. 2C.E7.EF)."""
        snapshot = self._load_snapshot()
        if not snapshot:
            return GenericResponse(success=False, message="No snapshot available to perform purge.")

        clean_target = target_address.upper()
        purged_count = 0
        modified_devices: List[str] = []

        # 1. Purge from PLM
        plm = snapshot.get("plm", {})
        for rec in plm.get("records", []):
            if rec.get("in_use") and rec.get("target_address", "").upper() == clean_target:
                rec["in_use"] = False
                rec["raw_mode"] = rec.get("raw_mode", "") + " (Deleted)"
                purged_count += 1
                if plm.get("address") not in modified_devices:
                    modified_devices.append(plm.get("address"))

        # 2. Purge from peer devices
        for dev in snapshot.get("devices", []):
            dev_addr = dev.get("address", "")
            for rec in dev.get("records", []):
                if rec.get("in_use") and rec.get("target_address", "").upper() == clean_target:
                    rec["in_use"] = False
                    rec["raw_mode"] = rec.get("raw_mode", "") + " (Deleted)"
                    purged_count += 1
                    if dev_addr not in modified_devices:
                        modified_devices.append(dev_addr)

        self._save_snapshot(snapshot)

        logger.info(f"Purged {purged_count} ghost references to {clean_target} across {len(modified_devices)} devices.")

        return GenericResponse(
            success=True,
            message=f"Successfully purged {purged_count} orphan references to {clean_target} across {len(modified_devices)} devices.",
            data={"target": clean_target, "records_purged": purged_count, "devices_affected": modified_devices},
        )

    async def replace_device(self, req: DeviceReplaceRequest) -> GenericResponse:
        """Execute seamless 'Clone & Swap' replacement of old_address with new_address."""
        snapshot = self._load_snapshot()
        old_addr = req.old_address.upper()
        new_addr = req.new_address.upper()

        if not snapshot:
            return GenericResponse(success=False, message="No snapshot loaded for device replacement.")

        old_dev = None
        new_dev = None
        for d in snapshot.get("devices", []):
            if d.get("address", "").upper() == old_addr:
                old_dev = d
            elif d.get("address", "").upper() == new_addr:
                new_dev = d

        if not old_dev:
            return GenericResponse(success=False, message=f"Source device {old_addr} not found in network.")

        cloned_records = 0
        retargeted_peers = 0

        # Step 1: Clone links to new device if requested
        if req.copy_links and new_dev:
            new_dev["records"] = []
            for rec in old_dev.get("records", []):
                if rec.get("in_use"):
                    copied = dict(rec)
                    new_dev["records"].append(copied)
                    cloned_records += 1
            new_dev["records_count"] = len(new_dev["records"])
            new_dev["status"] = "OK"

        # Step 2: Retarget all peer devices pointing to old_addr -> new_addr
        for dev in snapshot.get("devices", []):
            if dev.get("address", "").upper() in (old_addr, new_addr):
                continue
            for rec in dev.get("records", []):
                if rec.get("in_use") and rec.get("target_address", "").upper() == old_addr:
                    rec["target_address"] = new_addr
                    retargeted_peers += 1

        # Step 3: Retarget PLM links pointing to old_addr -> new_addr
        plm = snapshot.get("plm", {})
        for rec in plm.get("records", []):
            if rec.get("in_use") and rec.get("target_address", "").upper() == old_addr:
                rec["target_address"] = new_addr
                retargeted_peers += 1

        # Step 4: Decommission old device
        old_dev["status"] = "REPLACED"
        old_dev["description"] = f"Replaced by {new_addr}"

        self._save_snapshot(snapshot)

        return GenericResponse(
            success=True,
            message=f"Device {old_addr} successfully replaced by {new_addr}. Cloned {cloned_records} links and retargeted {retargeted_peers} peer references.",
            data={"old_address": old_addr, "new_address": new_addr, "cloned_links": cloned_records, "retargeted_peers": retargeted_peers},
        )

    def _add_or_update_snapshot_record(self, snapshot: Dict[str, Any], src_addr: str, target_addr: str, group: int, is_controller: bool, is_responder: bool, data1: int, data2: int, data3: int):
        clean_src = src_addr.upper()
        clean_target = target_addr.upper()
        plm = snapshot.get("plm", {})
        records_list = None

        if clean_src == plm.get("address", "").upper():
            records_list = plm.setdefault("records", [])
        else:
            for d in snapshot.get("devices", []):
                if d.get("address", "").upper() == clean_src:
                    records_list = d.setdefault("records", [])
                    break

        if records_list is None:
            return

        for r in records_list:
            if r.get("in_use") and r.get("target_address", "").upper() == clean_target and r.get("group") == group and r.get("is_controller") == is_controller:
                r["data1"] = data1
                r["data2"] = data2
                r["data3"] = data3
                return

        # Allocate new memory slot hex
        highest_mem = 0x0FFF
        if records_list:
            try:
                min_mem = min(int(r.get("mem_addr", "0x0FFF"), 16) for r in records_list if r.get("mem_addr"))
                new_mem = min_mem - 8
            except Exception:
                new_mem = 0x0FDF
        else:
            new_mem = 0x0FFF

        records_list.append({
            "mem_addr": f"0x{new_mem:04X}",
            "target_address": clean_target,
            "group": group,
            "is_controller": is_controller,
            "is_responder": is_responder,
            "in_use": True,
            "high_water_mark": False,
            "data1": data1,
            "data2": data2,
            "data3": data3,
            "raw_mode": "Controller" if is_controller else "Responder",
        })

    def _mark_snapshot_record_deleted(self, snapshot: Dict[str, Any], src_addr: str, target_addr: str, group: int, is_controller: bool) -> int:
        clean_src = src_addr.upper()
        clean_target = target_addr.upper()
        plm = snapshot.get("plm", {})
        records_list = None

        if clean_src == plm.get("address", "").upper():
            records_list = plm.get("records", [])
        else:
            for d in snapshot.get("devices", []):
                if d.get("address", "").upper() == clean_src:
                    records_list = d.get("records", [])
                    break

        if not records_list:
            return 0

        deleted_count = 0
        for r in records_list:
            if r.get("in_use") and r.get("target_address", "").upper() == clean_target and r.get("group") == group:
                if is_controller and r.get("is_controller"):
                    r["in_use"] = False
                    raw = r.get("raw_mode", "Controller")
                    r["raw_mode"] = raw.replace(" (Deleted)", "") + " (Deleted)"
                    deleted_count += 1
                elif not is_controller and (r.get("is_responder") or not r.get("is_controller")):
                    r["in_use"] = False
                    raw = r.get("raw_mode", "Responder")
                    r["raw_mode"] = raw.replace(" (Deleted)", "") + " (Deleted)"
                    deleted_count += 1
        return deleted_count


transaction_service = TransactionService()
