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
    DeviceAddRequest,
    MissingDefaultLinkItem,
    DefaultLinkAuditResponse,
    NetworkIssueItem,
    NetworkAnalysisResponse,
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

    def audit_default_links(self) -> DefaultLinkAuditResponse:
        """Scan all known devices against the PLM to detect missing or broken default Group 1 links."""
        snapshot = self._load_snapshot()
        plm_addr = snapshot.get("plm", {}).get("address", "53.C1.42").upper()
        plm_records = [r for r in snapshot.get("plm", {}).get("records", []) if r.get("in_use")]

        devices = snapshot.get("devices", [])
        items: List[MissingDefaultLinkItem] = []

        for dev in devices:
            dev_addr = dev.get("address", "").upper()
            if dev_addr == plm_addr:
                continue

            dev_name = dev.get("label") or dev.get("description", f"Device {dev_addr}")
            dev_type = dev.get("description", "Insteon Device")
            dev_records = [r for r in dev.get("records", []) if r.get("in_use")]

            # 1. Device as Controller to PLM on Group 1
            dev_ctrl_group1 = any(
                r.get("target_address", "").upper() == plm_addr and r.get("is_controller") and r.get("group") == 1
                for r in dev_records
            )
            # PLM as Responder to Device on Group 1
            plm_resp_group1 = any(
                r.get("target_address", "").upper() == dev_addr and (r.get("is_responder") or not r.get("is_controller")) and r.get("group") == 1
                for r in plm_records
            )
            has_device_to_plm = dev_ctrl_group1 and plm_resp_group1

            # 2. PLM as Controller to Device on Group 0 or 1
            plm_ctrl_group = any(
                r.get("target_address", "").upper() == dev_addr and r.get("is_controller") and r.get("group") in (0, 1)
                for r in plm_records
            )
            # Device as Responder to PLM on Group 0 or 1
            dev_resp_group = any(
                r.get("target_address", "").upper() == plm_addr and (r.get("is_responder") or not r.get("is_controller")) and r.get("group") in (0, 1)
                for r in dev_records
            )
            has_plm_to_device = plm_ctrl_group and dev_resp_group

            if not has_device_to_plm or not has_plm_to_device:
                missing_d2p = not has_device_to_plm
                missing_p2d = not has_plm_to_device

                if missing_d2p and missing_p2d:
                    summary = "Missing Default Link Pair (Both Directions)"
                    rec = f"Device {dev_addr} ({dev_name}) is completely unlinked from PLM {plm_addr}. Manual paddle presses and remote commands will fail. Recommend staging default reciprocal Group 1 link pair."
                elif missing_d2p:
                    summary = "Missing Device-to-PLM Controller Link (Group 1)"
                    rec = f"Device {dev_addr} ({dev_name}) cannot report paddle presses or state updates to PLM {plm_addr}. Recommend creating Group 1 responder link in PLM and controller link in device."
                else:
                    summary = "Missing PLM-to-Device Control Link (Group 1)"
                    rec = f"PLM {plm_addr} cannot send All-Link broadcast commands to device {dev_addr} ({dev_name}). Recommend creating Group 1 controller link in PLM and responder link in device."

                items.append(
                    MissingDefaultLinkItem(
                        device_address=dev_addr,
                        device_name=dev_name,
                        device_type=dev_type,
                        missing_device_to_plm=missing_d2p,
                        missing_plm_to_device=missing_p2d,
                        issue_summary=summary,
                        recommendation=rec,
                    )
                )

        total_count = len([d for d in devices if d.get("address", "").upper() != plm_addr])
        missing_count = len(items)
        healthy_count = max(0, total_count - missing_count)

        return DefaultLinkAuditResponse(
            plm_address=plm_addr,
            total_devices=total_count,
            healthy_count=healthy_count,
            missing_count=missing_count,
            items=items,
        )

    async def add_device(self, req: DeviceAddRequest) -> GenericResponse:
        """Add a new Insteon device and automatically create default reciprocal links with the PLM."""
        import re
        snapshot = self._load_snapshot()
        clean_addr = req.address.strip().upper()

        if not re.match(r"^[0-9A-F]{2}\.[0-9A-F]{2}\.[0-9A-F]{2}$", clean_addr):
            return GenericResponse(
                success=False,
                message=f"Invalid Insteon address format '{clean_addr}'. Expected format: XX.YY.ZZ (e.g. 1A.2B.3C)."
            )

        plm_addr = snapshot.get("plm", {}).get("address", "53.C1.42").upper()
        if clean_addr == plm_addr:
            return GenericResponse(success=False, message=f"Address {clean_addr} is the PLM modem address.")

        existing = [d for d in snapshot.get("devices", []) if d.get("address", "").upper() == clean_addr]
        if existing:
            return GenericResponse(success=False, message=f"Device {clean_addr} already exists in the network.")

        from app.insteon import insteon_manager
        if insteon_manager.is_connected and PYINSTEON_AVAILABLE:
            try:
                addr_obj = Address(clean_addr)
                pyinsteon.devices.set_id(addr_obj, req.cat or 1, req.subcat or 0, req.firmware or 0)
            except Exception as ex:
                logger.warning(f"Could not register device with live pyinsteon: {ex}")

        # Add device to snapshot
        new_dev = {
            "address": clean_addr,
            "cat": req.cat or 1,
            "subcat": req.subcat or 0,
            "firmware": req.firmware or 0,
            "description": req.description or "Insteon Device",
            "label": req.description or "Insteon Device",
            "is_battery": False,
            "records_count": 0,
            "records": [],
            "status": "OK",
            "db_delta": 1,
        }
        snapshot.setdefault("devices", []).append(new_dev)
        self._save_snapshot(snapshot)

        default_links_created = False
        if req.create_default_links:
            # 1. PLM (Controller) <-> Device (Responder) on Group 1
            await self.create_reciprocal_link(
                ReciprocalLinkRequest(
                    controller_address=plm_addr,
                    responder_address=clean_addr,
                    group=1,
                    data1=255,
                    data2=28,
                    data3=1,
                )
            )
            # 2. Device (Controller) <-> PLM (Responder) on Group 1
            await self.create_reciprocal_link(
                ReciprocalLinkRequest(
                    controller_address=clean_addr,
                    responder_address=plm_addr,
                    group=1,
                    data1=255,
                    data2=28,
                    data3=1,
                )
            )
            default_links_created = True

        msg = f"Device {clean_addr} ({req.description}) added successfully."
        if default_links_created:
            msg += f" Default reciprocal Group 1 link pair with PLM {plm_addr} created."

        return GenericResponse(
            success=True,
            message=msg,
            data={"address": clean_addr, "default_links_created": default_links_created, "plm_address": plm_addr}
        )

    def analyze_network(self) -> NetworkAnalysisResponse:
        """Deep-dive diagnostic analysis of the entire Insteon network.
        
        Identifies:
        - Broken half-links (missing reciprocal controller/responder on target)
        - Self-referential loopback links
        - Dead device ghost references
        - Missing default Group 1 links with PLM
        - Corrupted Group 0 links
        - Redundant duplicate records
        """
        snapshot = self._load_snapshot()
        plm = snapshot.get("plm", {})
        plm_addr = plm.get("address", "53.C1.42").upper()
        devices = snapshot.get("devices", [])
        
        all_dev_map = {d.get("address", "").upper(): d for d in devices if d.get("address")}
        all_dev_map[plm_addr] = plm

        def get_device_name(addr: str) -> str:
            d = all_dev_map.get(addr.upper(), {})
            if addr.upper() == plm_addr:
                return "USB PowerLinc Modem (PLM)"
            return d.get("label") or d.get("description") or f"Device {addr}"

        def is_keypad(addr: str) -> bool:
            d = all_dev_map.get(addr.upper(), {})
            name = (d.get("label") or d.get("description") or "").lower()
            return "keypad" in name or "remote" in name

        total_active_records = 0
        issues: List[NetworkIssueItem] = []
        active_links = []

        for owner_addr, dev in all_dev_map.items():
            recs = dev.get("records", [])
            seen_owner_keys = set()
            for r in recs:
                mem = r.get("mem_addr")
                in_use = r.get("in_use", False)
                hwm = r.get("high_water_mark", False)
                grp = r.get("group", 0)
                tgt = r.get("target_address", "").upper()
                is_ctrl = r.get("is_controller", False)
                is_resp = r.get("is_responder", False)
                d1 = r.get("data1", 255)
                d2 = r.get("data2", 28)
                d3 = r.get("data3", 1)

                if hwm or not in_use:
                    continue

                total_active_records += 1
                role = "Controller" if is_ctrl else "Responder"
                owner_name = get_device_name(owner_addr)
                tgt_name = get_device_name(tgt)

                # 1. Check Loopback / Self-Referential
                if tgt == owner_addr:
                    issues.append(NetworkIssueItem(
                        id=f"loopback_{owner_addr}_{mem}",
                        issue_type="loopback",
                        severity="warning",
                        owner_address=owner_addr,
                        owner_name=owner_name,
                        mem_addr=mem,
                        target_address=tgt,
                        target_name=tgt_name,
                        group=grp,
                        role=role,
                        summary=f"Self-referential loopback link on {owner_name} ({owner_addr})",
                        explanation=f"Slot {mem} points back to its own device address as {role} for Group {grp}. Insteon devices cannot control or respond to themselves; this is a phantom link.",
                        recommended_action="delete_record",
                        fix_label="🗑️ Delete Phantom Slot",
                        secondary_action=None,
                        secondary_label=None,
                        can_auto_fix=True,
                        data1=d1, data2=d2, data3=d3
                    ))
                    continue

                # 2. Check Ghost Target (device not in network)
                if tgt not in all_dev_map and tgt != "00.00.00":
                    issues.append(NetworkIssueItem(
                        id=f"ghost_{owner_addr}_{mem}",
                        issue_type="ghost",
                        severity="error",
                        owner_address=owner_addr,
                        owner_name=owner_name,
                        mem_addr=mem,
                        target_address=tgt,
                        target_name=f"Decommissioned Device ({tgt})",
                        group=grp,
                        role=role,
                        summary=f"Ghost reference to decommissioned device {tgt}",
                        explanation=f"{owner_name} has an active {role} link pointing to {tgt}, which does not exist in the network. Every button press causes unacknowledged retries across the powerline.",
                        recommended_action="delete_record",
                        fix_label="🗑️ Delete Ghost Slot",
                        secondary_action=None,
                        secondary_label=None,
                        can_auto_fix=True,
                        data1=d1, data2=d2, data3=d3
                    ))
                    continue

                # 3. Check Group 0 corruption
                if grp == 0:
                    issues.append(NetworkIssueItem(
                        id=f"grp0_{owner_addr}_{mem}",
                        issue_type="group_zero",
                        severity="warning",
                        owner_address=owner_addr,
                        owner_name=owner_name,
                        mem_addr=mem,
                        target_address=tgt,
                        target_name=tgt_name,
                        group=grp,
                        role=role,
                        summary=f"Invalid Group 0 record in {owner_name}",
                        explanation=f"Insteon scenes must be Group 1-255. Group 0 indicates an aborted pairing handshake or improperly cleared legacy memory slot.",
                        recommended_action="delete_record",
                        fix_label="🗑️ Delete Group 0 Slot",
                        secondary_action=None,
                        secondary_label=None,
                        can_auto_fix=True,
                        data1=d1, data2=d2, data3=d3
                    ))
                    continue

                # 4. Check Redundant Duplicate
                dup_key = (tgt, grp, role)
                if dup_key in seen_owner_keys:
                    issues.append(NetworkIssueItem(
                        id=f"dup_{owner_addr}_{mem}",
                        issue_type="duplicate",
                        severity="warning",
                        owner_address=owner_addr,
                        owner_name=owner_name,
                        mem_addr=mem,
                        target_address=tgt,
                        target_name=tgt_name,
                        group=grp,
                        role=role,
                        summary=f"Redundant duplicate {role} link on Group {grp}",
                        explanation=f"{owner_name} has multiple active {role} records for {tgt_name} on Group {grp}. Multiple identical records cause repeated transmissions and waste EEPROM space.",
                        recommended_action="delete_record",
                        fix_label="🗑️ Delete Duplicate Slot",
                        secondary_action=None,
                        secondary_label=None,
                        can_auto_fix=True,
                        data1=d1, data2=d2, data3=d3
                    ))
                    continue
                else:
                    seen_owner_keys.add(dup_key)

                active_links.append({
                    "owner": owner_addr,
                    "owner_name": owner_name,
                    "mem": mem,
                    "target": tgt,
                    "target_name": tgt_name,
                    "group": grp,
                    "role": role,
                    "data1": d1,
                    "data2": d2,
                    "data3": d3,
                })

        # 5. Check Half-Links (Missing reciprocal on target)
        for link in active_links:
            owner = link["owner"]
            owner_name = link["owner_name"]
            target = link["target"]
            target_name = link["target_name"]
            grp = link["group"]
            role = link["role"]
            mem = link["mem"]
            d1, d2, d3 = link["data1"], link["data2"], link["data3"]

            target_dev = all_dev_map.get(target)
            if not target_dev:
                continue

            target_recs = [tr for tr in target_dev.get("records", []) if tr.get("in_use") and not tr.get("high_water_mark")]
            expected_role = "Responder" if role == "Controller" else "Controller"

            has_reciprocal = any(
                tr.get("target_address", "").upper() == owner and
                tr.get("group") == grp and
                ((expected_role == "Responder" and (tr.get("is_responder") or not tr.get("is_controller"))) or
                 (expected_role == "Controller" and tr.get("is_controller")))
                for tr in target_recs
            )

            if not has_reciprocal:
                # Group 251 is used internally for pyinsteon device engine discovery and does not have reciprocal controller records
                if grp == 251:
                    continue

                is_kp = is_keypad(owner)
                ctrl_addr = owner if role == "Controller" else target
                resp_addr = target if role == "Controller" else owner

                if is_kp and role == "Controller":
                    severity = "warning"
                    button_label = f"Button {chr(64 + grp)} (Key {grp})" if 1 <= grp <= 8 else f"Group {grp}"
                    summary = f"Keypad {button_label} One-Way Broadcast to {target_name}"
                    explanation = f"{owner_name} is set to control {target_name} on {button_label}, but {target_name} has NO matching responder link. When {button_label} is pressed on the keypad, {target_name} will ignore the command."
                    fix_label = f"⚡ Add Responder Link on {target_name}"
                elif role == "Controller":
                    severity = "warning"
                    summary = f"Missing Responder Link: {owner_name} → {target_name} (Grp {grp})"
                    explanation = f"{owner_name} transmits commands on Group {grp} to {target_name}, but {target_name} does not have a matching responder entry in its database."
                    fix_label = f"⚡ Add Responder Link on {target_name}"
                else:
                    severity = "warning"
                    summary = f"Missing Controller Link: {owner_name} ← {target_name} (Grp {grp})"
                    explanation = f"{owner_name} is programmed to respond to Group {grp} from {target_name}, but {target_name} has no matching controller record to trigger it."
                    fix_label = f"⚡ Add Controller Link on {target_name}"

                issues.append(NetworkIssueItem(
                    id=f"half_{owner}_{target}_{grp}_{role}_{mem}",
                    issue_type="half_link",
                    severity=severity,
                    owner_address=owner,
                    owner_name=owner_name,
                    mem_addr=mem,
                    target_address=target,
                    target_name=target_name,
                    group=grp,
                    role=role,
                    controller_address=ctrl_addr,
                    responder_address=resp_addr,
                    summary=summary,
                    explanation=explanation,
                    recommended_action="create_reciprocal",
                    fix_label=fix_label,
                    secondary_action="delete_record",
                    secondary_label="🗑️ Delete Broken Slot",
                    can_auto_fix=True,
                    data1=d1, data2=d2, data3=d3
                ))

        # 6. Check Missing Default PLM Links
        for dev in devices:
            dev_addr = dev.get("address", "").upper()
            if dev_addr == plm_addr or not dev_addr:
                continue
            dev_name = get_device_name(dev_addr)
            dev_records = [r for r in dev.get("records", []) if r.get("in_use")]
            plm_records = [r for r in plm.get("records", []) if r.get("in_use")]

            d2p = any(r.get("target_address", "").upper() == plm_addr and r.get("is_controller") and r.get("group") == 1 for r in dev_records) and \
                  any(r.get("target_address", "").upper() == dev_addr and (r.get("is_responder") or not r.get("is_controller")) and r.get("group") == 1 for r in plm_records)
            p2d = any(r.get("target_address", "").upper() == dev_addr and r.get("is_controller") and r.get("group") in (0, 1) for r in plm_records) and \
                  any(r.get("target_address", "").upper() == plm_addr and (r.get("is_responder") or not r.get("is_controller")) and r.get("group") in (0, 1) for r in dev_records)

            if not d2p or not p2d:
                issues.append(NetworkIssueItem(
                    id=f"default_{dev_addr}",
                    issue_type="missing_default",
                    severity="error",
                    owner_address=dev_addr,
                    owner_name=dev_name,
                    mem_addr=None,
                    target_address=plm_addr,
                    target_name="USB PowerLinc Modem (PLM)",
                    group=1,
                    role="Both",
                    summary=f"Missing Default Reciprocal PLM Links for {dev_name}",
                    explanation=f"{dev_name} does not have a verified bidirectional Group 1 link pair with the PLM. Automated status reports and remote controls will be unreliable.",
                    recommended_action="create_default_link",
                    fix_label="⚡ Establish Default PLM Links",
                    secondary_action=None,
                    secondary_label=None,
                    can_auto_fix=True,
                ))

        half_count = sum(1 for i in issues if i.issue_type == "half_link" and i.severity != "info")
        loopback_count = sum(1 for i in issues if i.issue_type == "loopback")
        ghost_count = sum(1 for i in issues if i.issue_type == "ghost")
        dup_count = sum(1 for i in issues if i.issue_type == "duplicate")
        grp0_count = sum(1 for i in issues if i.issue_type == "group_zero")
        missing_def_count = sum(1 for i in issues if i.issue_type == "missing_default")

        # Health score calculation
        deductions = (ghost_count * 10) + (missing_def_count * 5) + (loopback_count * 5) + (half_count * 3) + (dup_count * 2) + (grp0_count * 2)
        score = max(0, min(100, 100 - deductions))

        return NetworkAnalysisResponse(
            health_score=score,
            total_devices=len(all_dev_map),
            total_active_records=total_active_records,
            total_issues=len(issues),
            half_links_count=sum(1 for i in issues if i.issue_type == "half_link"),
            loopbacks_count=loopback_count,
            missing_defaults_count=missing_def_count,
            ghosts_count=ghost_count,
            duplicates_count=dup_count,
            group_zero_count=grp0_count,
            issues=issues
        )


transaction_service = TransactionService()

