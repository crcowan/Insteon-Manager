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
        default_path = os.path.join("backend", "network_snapshot.json") if os.path.isdir("backend") else "network_snapshot.json"
        path = self._find_snapshot_file() or default_path
        try:
            dir_name = os.path.dirname(path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            # Keep all workspace network_snapshot.json locations in sync
            for sync_candidate in ("network_snapshot.json", "backend/network_snapshot.json", "../network_snapshot.json"):
                if os.path.isfile(sync_candidate) and os.path.abspath(sync_candidate) != os.path.abspath(path):
                    with open(sync_candidate, "w", encoding="utf-8") as rf:
                        json.dump(data, rf, indent=2)
        except Exception as ex:
            logger.error(f"Error saving snapshot: {ex}")

    def _compute_record_data_bytes(
        self,
        snapshot: Dict[str, Any],
        plm_addr: str,
        owner_addr: str,
        target_addr: str,
        group: int,
        is_controller: bool,
        req_d1: int = 255,
        req_d2: int = 28,
        req_d3: int = 1,
    ) -> Tuple[int, int, int]:
        """Compute protocol-accurate (data1, data2, data3) bytes for an Insteon ALDB record.

        - PLM records (Controller or Responder): (target_cat, target_subcat, target_firmware)
        - Device Controller records: (0x03 retries, 0x00, button_or_group 1..8)
        - Device Responder records on Group 0 to PLM (I2CS Auth): (plm_cat, plm_subcat, plm_firmware)
        - Device Responder records on Group 1..255: (on_level 0..255, ramp_rate 0..31, load_or_button 1..8)
        """
        clean_owner = owner_addr.upper()
        clean_target = target_addr.upper()
        clean_plm = plm_addr.upper()

        # Case 1: Record lives in the PLM Modem
        if clean_owner == clean_plm:
            target_dev = next(
                (d for d in snapshot.get("devices", []) if d.get("address", "").upper() == clean_target),
                {},
            )
            cat = int(target_dev.get("cat", 1) or 1)
            subcat = int(target_dev.get("subcat", 0) or 0)
            fw = int(target_dev.get("firmware", 0) or (group if not is_controller else 0))
            return (cat, subcat, fw)

        # Case 2: Controller record on a physical Switch / Dimmer / Keypad
        if is_controller:
            # data1 = 3 (standard All-Link cleanup retry count), data2 = 0, data3 = button/group (1..8)
            btn = req_d3 if (req_d3 and 1 <= req_d3 <= 8) else (group if 1 <= group <= 8 else 1)
            return (3, 0, btn)

        # Case 3: Responder record on a physical Switch / Dimmer / Keypad
        if group == 0 and clean_target == clean_plm:
            # I2CS PLM Authorization record
            plm_info = snapshot.get("plm", {})
            p_cat = int(plm_info.get("cat", 3) or 3)
            p_sub = int(plm_info.get("subcat", 21) or 21)
            p_fw = int(plm_info.get("firmware", 158) or 158)
            return (p_cat, p_sub, p_fw)

        on_level = max(0, min(255, int(req_d1 if req_d1 is not None else 255)))
        ramp_rate = max(0, min(31, int(req_d2 if req_d2 is not None else 28)))
        load_btn = int(req_d3 if (req_d3 and 1 <= req_d3 <= 255) else 1)
        return (on_level, ramp_rate, load_btn)

    async def create_reciprocal_link(self, req: ReciprocalLinkRequest) -> GenericResponse:
        """Atomically create a reciprocal Controller <-> Responder link pair.
        
        If either write fails, the other is rolled back automatically.
        """
        snapshot = self._load_snapshot()
        plm_addr = snapshot.get("plm", {}).get("address", "53.C1.42").upper()
        ctrl_addr = req.controller_address.strip().upper()
        resp_addr = req.responder_address.strip().upper()
        grp = req.group

        ctrl_d1, ctrl_d2, ctrl_d3 = self._compute_record_data_bytes(
            snapshot, plm_addr, ctrl_addr, resp_addr, grp, True, req.data1, req.data2, req.data3
        )
        resp_d1, resp_d2, resp_d3 = self._compute_record_data_bytes(
            snapshot, plm_addr, resp_addr, ctrl_addr, grp, False, req.data1, req.data2, req.data3
        )

        logger.info(
            f"Initiating atomic reciprocal link: {ctrl_addr} (Ctrl d={ctrl_d1},{ctrl_d2},{ctrl_d3}) "
            f"<---> {resp_addr} (Resp d={resp_d1},{resp_d2},{resp_d3}) for Group {grp}"
        )

        from app.insteon import insteon_manager
        live_hw = insteon_manager.is_connected and not insteon_manager._mock_mode and PYINSTEON_AVAILABLE

        # Step 1: Pre-flight reachability & ALDB seeding if live hardware is active
        if live_hw:
            addr_ctrl = Address(ctrl_addr)
            addr_resp = Address(resp_addr)

            from pyinsteon.constants import ALDBStatus, ReadWriteMode

            ok_resp, dev_resp, msg_resp = await insteon_manager.ensure_device_aldb_ready_for_write(resp_addr, snapshot)
            if not ok_resp or not dev_resp:
                logger.error(f"Responder pre-check failed on {resp_addr}: {msg_resp}")
                return GenericResponse(
                    success=False,
                    message=f"Responder pre-check failed: {msg_resp} Link creation aborted without changes.",
                )

            ok_ctrl, dev_ctrl, msg_ctrl = await insteon_manager.ensure_device_aldb_ready_for_write(ctrl_addr, snapshot)
            if not ok_ctrl or not dev_ctrl:
                logger.error(f"Controller pre-check failed on {ctrl_addr}: {msg_ctrl}")
                return GenericResponse(
                    success=False,
                    message=f"Controller pre-check failed: {msg_ctrl} Link creation aborted without changes.",
                )

            # Step 2: Write Controller record
            dev_ctrl.aldb._status = ALDBStatus.LOADED
            dev_ctrl.aldb.add(
                group=grp,
                target=addr_resp,
                controller=True,
                data1=ctrl_d1,
                data2=ctrl_d2,
                data3=ctrl_d3,
            )
            ctrl_done, ctrl_failed = await dev_ctrl.aldb.async_write(force=True)
            if ctrl_failed:
                if getattr(dev_ctrl.aldb, "_read_write_mode", None) != ReadWriteMode.PEEK_POKE:
                    logger.info(f"Standard write failed on controller {ctrl_addr}; retrying with legacy PEEK/POKE mode...")
                    dev_ctrl.aldb._read_write_mode = ReadWriteMode.PEEK_POKE
                    dev_ctrl.aldb._status = ALDBStatus.LOADED
                    ctrl_done, ctrl_failed = await dev_ctrl.aldb.async_write(force=True)
            if ctrl_failed:
                dev_ctrl.aldb.clear_pending()
                logger.error(f"Hardware write failed while creating Controller record on {ctrl_addr} (target {resp_addr}, Grp {grp}).")
                return GenericResponse(success=False, message=f"Failed writing Controller record to {ctrl_addr}")
            logger.info(f"Hardware EEPROM write succeeded on controller {ctrl_addr}: Controller link to {resp_addr} (Grp {grp}) added.")

            # Step 3: Write Responder record
            dev_resp.aldb._status = ALDBStatus.LOADED
            dev_resp.aldb.add(
                group=grp,
                target=addr_ctrl,
                controller=False,
                data1=resp_d1,
                data2=resp_d2,
                data3=resp_d3,
            )
            resp_done, resp_failed = await dev_resp.aldb.async_write(force=True)
            if resp_failed:
                if getattr(dev_resp.aldb, "_read_write_mode", None) != ReadWriteMode.PEEK_POKE:
                    logger.info(f"Standard write failed on responder {resp_addr}; retrying with legacy PEEK/POKE mode...")
                    dev_resp.aldb._read_write_mode = ReadWriteMode.PEEK_POKE
                    dev_resp.aldb._status = ALDBStatus.LOADED
                    resp_done, resp_failed = await dev_resp.aldb.async_write(force=True)
            if resp_failed:
                logger.warning(f"Responder write failed on {resp_addr}; initiating ROLLBACK on controller {ctrl_addr}...")
                dev_resp.aldb.clear_pending()
                for rec in dev_ctrl.aldb.find(target=addr_resp, group=grp, is_controller=True, in_use=True):
                    dev_ctrl.aldb.modify(mem_addr=rec.mem_addr, in_use=False)
                dev_ctrl.aldb._status = ALDBStatus.LOADED
                await dev_ctrl.aldb.async_write(force=True)
                logger.error(f"Hardware write failed on responder {resp_addr} (target {ctrl_addr}, Grp {grp}). Controller link on {ctrl_addr} rolled back.")
                return GenericResponse(
                    success=False,
                    message=f"Write to responder {resp_addr} failed. Controller link on {ctrl_addr} rolled back cleanly.",
                )
            logger.info(f"Hardware EEPROM write succeeded on responder {resp_addr}: Responder link to {ctrl_addr} (Grp {grp}) added.")

            await insteon_manager.refresh_device_db_delta_after_write(ctrl_addr, snapshot)
            await insteon_manager.refresh_device_db_delta_after_write(resp_addr, snapshot)

        # Step 4: Update snapshot records in cache
        if snapshot:
            self._add_or_update_snapshot_record(
                snapshot=snapshot,
                src_addr=ctrl_addr,
                target_addr=resp_addr,
                group=grp,
                is_controller=True,
                is_responder=False,
                data1=ctrl_d1,
                data2=ctrl_d2,
                data3=ctrl_d3,
            )
            self._add_or_update_snapshot_record(
                snapshot=snapshot,
                src_addr=resp_addr,
                target_addr=ctrl_addr,
                group=grp,
                is_controller=False,
                is_responder=True,
                data1=resp_d1,
                data2=resp_d2,
                data3=resp_d3,
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
        ctrl_clean = ctrl_addr.strip().upper()
        resp_clean = resp_addr.strip().upper()
        plm_addr = snapshot.get("plm", {}).get("address", "53.C1.42").upper()

        # IMMUTABLE GUARD: Core PLM links (Group 0 I2CS Auth and Group 1 Status/Control) must never be deleted
        if (ctrl_clean == plm_addr or resp_clean == plm_addr) and group in (0, 1):
            logger.warning(
                f"BLOCKED: Attempt to delete protected core PLM link between {ctrl_clean} and {resp_clean} for Group {group}."
            )
            return GenericResponse(
                success=False,
                message=(
                    f"Protected Link Violation: Reciprocal links between devices and the PLM Modem ({plm_addr}) "
                    f"for Group {group} are essential for openHAB operation and I2CS communication, and cannot be deleted."
                ),
            )

        logger.info(f"Removing reciprocal link between {ctrl_clean} and {resp_clean} for Group {group}...")

        from app.insteon import insteon_manager
        live_hw = insteon_manager.is_connected and not insteon_manager._mock_mode and PYINSTEON_AVAILABLE
        if live_hw:
            addr_ctrl = Address(ctrl_clean)
            addr_resp = Address(resp_clean)

            ok_ctrl, dev_ctrl, msg_ctrl = await insteon_manager.ensure_device_aldb_ready_for_write(ctrl_clean, snapshot)
            if not ok_ctrl or not dev_ctrl:
                return GenericResponse(success=False, message=f"Cannot delete link: {msg_ctrl}")

            ok_resp, dev_resp, msg_resp = await insteon_manager.ensure_device_aldb_ready_for_write(resp_clean, snapshot)
            if not ok_resp or not dev_resp:
                return GenericResponse(success=False, message=f"Cannot delete link: {msg_resp}")

            from pyinsteon.constants import ALDBStatus, ReadWriteMode
            dev_ctrl.aldb._status = ALDBStatus.LOADED
            for rec in dev_ctrl.aldb.find(target=addr_resp, group=group, is_controller=True, in_use=True):
                dev_ctrl.aldb.modify(mem_addr=rec.mem_addr, in_use=False)
            _, ctrl_failed = await dev_ctrl.aldb.async_write(force=True)
            if ctrl_failed and getattr(dev_ctrl.aldb, "_read_write_mode", None) != ReadWriteMode.PEEK_POKE:
                dev_ctrl.aldb._read_write_mode = ReadWriteMode.PEEK_POKE
                dev_ctrl.aldb._status = ALDBStatus.LOADED
                _, ctrl_failed = await dev_ctrl.aldb.async_write(force=True)
            if ctrl_failed:
                return GenericResponse(success=False, message=f"Hardware write failed while deleting Controller record on {ctrl_clean}.")

            dev_resp.aldb._status = ALDBStatus.LOADED
            for rec in dev_resp.aldb.find(target=addr_ctrl, group=group, is_controller=False, in_use=True):
                dev_resp.aldb.modify(mem_addr=rec.mem_addr, in_use=False)
            _, resp_failed = await dev_resp.aldb.async_write(force=True)
            if resp_failed and getattr(dev_resp.aldb, "_read_write_mode", None) != ReadWriteMode.PEEK_POKE:
                dev_resp.aldb._read_write_mode = ReadWriteMode.PEEK_POKE
                dev_resp.aldb._status = ALDBStatus.LOADED
                _, resp_failed = await dev_resp.aldb.async_write(force=True)
            if resp_failed:
                return GenericResponse(success=False, message=f"Hardware write failed while deleting Responder record on {resp_clean}.")

            await insteon_manager.refresh_device_db_delta_after_write(ctrl_clean, snapshot)
            await insteon_manager.refresh_device_db_delta_after_write(resp_clean, snapshot)

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

    async def delete_device_aldb_record(self, address: str, mem_addr: Any) -> GenericResponse:
        """Mark an individual ALDB slot deleted (in_use=False) on a device."""
        snapshot = self._load_snapshot()
        clean_addr = address.strip().upper()
        clean_mem = f"0x{mem_addr:04X}".upper() if isinstance(mem_addr, int) else str(mem_addr).strip().upper()

        logger.info(f"Deleting ALDB slot {clean_mem} on device {clean_addr}...")

        # Locate the target record metadata in snapshot first
        target_snap_rec = None
        records_list = None
        plm_addr = snapshot.get("plm", {}).get("address", "53.C1.42").upper() if snapshot else "53.C1.42"
        if snapshot:
            if clean_addr == plm_addr:
                records_list = snapshot.get("plm", {}).get("records", [])
            else:
                for d in snapshot.get("devices", []):
                    if d.get("address", "").upper() == clean_addr:
                        records_list = d.get("records", [])
                        break
            if records_list:
                for r in records_list:
                    if r.get("mem_addr", "").upper() == clean_mem:
                        target_snap_rec = r
                        break

        # IMMUTABLE GUARD: Core PLM links (Group 0 I2CS Auth and Group 1 Status/Control) must never be deleted
        if target_snap_rec is not None:
            t_addr = str(target_snap_rec.get("target_address", "")).upper()
            t_grp = int(target_snap_rec.get("group", 0))
            if (clean_addr == plm_addr or t_addr == plm_addr) and t_grp in (0, 1):
                logger.warning(
                    f"BLOCKED: Attempt to delete protected PLM link slot {clean_mem} on {clean_addr} (target {t_addr}, Group {t_grp})."
                )
                return GenericResponse(
                    success=False,
                    message=(
                        f"Protected Record Violation: Slot {clean_mem} on {clean_addr} is a core Group {t_grp} "
                        f"PLM communication link and cannot be deleted."
                    ),
                )

        from app.insteon import insteon_manager
        live_hw = insteon_manager.is_connected and not insteon_manager._mock_mode and PYINSTEON_AVAILABLE
        if live_hw:
            ok, dev_obj, err_msg = await insteon_manager.ensure_device_aldb_ready_for_write(clean_addr, snapshot)
            if not ok or not dev_obj:
                return GenericResponse(success=False, message=err_msg)

            mem_int = int(clean_mem, 16) if clean_mem.startswith("0X") else int(clean_mem)
            rec = dev_obj.aldb.get(mem_int)

            # On PLM ModemALDB, synthetic 0x1FFF addresses may shift across sessions; match by (target, group, controller) fallback
            if rec is None and target_snap_rec is not None:
                t_addr = Address(target_snap_rec.get("target_address", "00.00.00"))
                t_grp = int(target_snap_rec.get("group", 0))
                t_ctrl = bool(target_snap_rec.get("is_controller", False))
                matches = list(dev_obj.aldb.find(target=t_addr, group=t_grp, is_controller=t_ctrl, in_use=True))
                if matches:
                    rec = matches[0]
                    mem_int = rec.mem_addr

            if not rec:
                return GenericResponse(
                    success=False,
                    message=f"Slot {clean_mem} was not found in live ALDB for {clean_addr}.",
                )

            # IMMUTABLE GUARD: Verify live record is not a protected PLM link
            rec_target = str(rec.target).upper()
            rec_group = int(getattr(rec, "group", 0))
            if (clean_addr == plm_addr or rec_target == plm_addr) and rec_group in (0, 1):
                logger.warning(
                    f"BLOCKED: Attempt to delete live protected PLM link slot {clean_mem} on {clean_addr} (target {rec_target}, Group {rec_group})."
                )
                return GenericResponse(
                    success=False,
                    message=(
                        f"Protected Record Violation: Live slot {clean_mem} on {clean_addr} is a core Group {rec_group} "
                        f"PLM communication link and cannot be deleted."
                    ),
                )

            # Safety check: Verify the live slot actually contains the intended target/group before deleting
            if target_snap_rec is not None:
                exp_tgt = str(target_snap_rec.get("target_address", "")).upper()
                live_tgt = str(rec.target).upper()
                if exp_tgt and live_tgt != exp_tgt:
                    logger.error(
                        f"Safety abort: Slot {clean_mem} on {clean_addr} contains target {live_tgt}, "
                        f"which does not match expected target {exp_tgt}. Aborting deletion to prevent data loss."
                    )
                    return GenericResponse(
                        success=False,
                        message=f"Safety abort: Slot {clean_mem} holds target {live_tgt}, expected {exp_tgt}.",
                    )

            dev_obj.aldb.modify(mem_addr=mem_int, in_use=False)
            logger.info(f"Hardware EEPROM: Marked slot {clean_mem} (Group {getattr(rec, 'group', '?')}, target {getattr(rec, 'target', '?')}) deleted on {clean_addr}.")
            _, failed = await dev_obj.aldb.async_write(force=True)
            if failed:
                dev_obj.aldb.clear_pending()
                return GenericResponse(
                    success=False,
                    message=f"Hardware EEPROM write failed while deleting slot {clean_mem} on {clean_addr}.",
                )
            logger.info(f"Hardware EEPROM write succeeded on {clean_addr}: Slot {clean_mem} marked deleted.")
            await insteon_manager.refresh_device_db_delta_after_write(clean_addr, snapshot)

        found = False
        if target_snap_rec is not None:
            target_snap_rec["in_use"] = False
            raw = target_snap_rec.get("raw_mode", "Record")
            target_snap_rec["raw_mode"] = raw.replace(" (Deleted)", "") + " (Deleted)"
            found = True
            logger.info(f"Deleted slot {clean_mem} on {clean_addr} (Group {target_snap_rec.get('group', '?')}, target {target_snap_rec.get('target_address', '?')}) in snapshot.")
            self._save_snapshot(snapshot)

        return GenericResponse(
            success=found or live_hw,
            message=f"Slot {clean_mem} on device {clean_addr} marked deleted successfully." if (found or live_hw) else f"Slot {clean_mem} not found on {clean_addr}.",
        )

    async def purge_ghost_links(self, target_address: str) -> GenericResponse:
        """Eradicate all references across the entire network pointing to an offline/retired device (e.g. 2C.E7.EF)."""
        snapshot = self._load_snapshot()
        if not snapshot:
            return GenericResponse(success=False, message="No snapshot available to perform purge.")

        clean_target = target_address.strip().upper()
        purged_count = 0
        modified_devices: List[str] = []

        plm = snapshot.get("plm", {})
        plm_addr = plm.get("address", "53.C1.42").upper()

        if clean_target == plm_addr or clean_target in ("53.C1.42", "00.00.00", "000000"):
            logger.warning(f"Rejected attempt to purge primary PLM Hub modem ({clean_target}) as a ghost target.")
            return GenericResponse(
                success=False,
                message=f"Aborted: {clean_target} is the primary PLM Hub modem and cannot be purged as a ghost target.",
            )

        from app.insteon import insteon_manager
        live_hw = insteon_manager.is_connected and not insteon_manager._mock_mode and PYINSTEON_AVAILABLE

        # Identify which devices have active links to clean_target
        owners_to_purge: List[str] = []

        if any(r.get("in_use") and r.get("target_address", "").upper() == clean_target for r in plm.get("records", [])):
            owners_to_purge.append(plm_addr)

        for dev in snapshot.get("devices", []):
            dev_addr = dev.get("address", "").upper()
            if dev_addr and any(r.get("in_use") and r.get("target_address", "").upper() == clean_target for r in dev.get("records", [])):
                owners_to_purge.append(dev_addr)

        # Execute live hardware deletions if connected
        hardware_success_devices: List[str] = []
        hardware_failed_devices: List[str] = []
        if live_hw and owners_to_purge:
            ghost_addr_obj = Address(clean_target)
            for owner_addr in owners_to_purge:
                ok, dev_obj, err_msg = await insteon_manager.ensure_device_aldb_ready_for_write(owner_addr, snapshot)
                if not ok or not dev_obj:
                    logger.warning(f"Skipping ghost purge on {owner_addr}: {err_msg}")
                    hardware_failed_devices.append(f"{owner_addr} ({err_msg})")
                    continue
                modified_slots = 0
                for mem_k, rec in list(dev_obj.aldb.items()):
                    if rec.is_in_use and str(rec.target).upper() == str(ghost_addr_obj).upper():
                        dev_obj.aldb.modify(mem_addr=mem_k, in_use=False)
                        modified_slots += 1
                        logger.info(f"Hardware EEPROM: Marked slot 0x{mem_k:04X} (Group {rec.group}, target {clean_target}) deleted on {owner_addr}.")
                if modified_slots > 0:
                    from pyinsteon.constants import ALDBStatus, ReadWriteMode
                    dev_obj.aldb._status = ALDBStatus.LOADED
                    _, failed = await dev_obj.aldb.async_write(force=True)
                    if failed:
                        if getattr(dev_obj.aldb, "_read_write_mode", None) != ReadWriteMode.PEEK_POKE:
                            logger.info(f"Standard write failed on {owner_addr}; retrying with legacy PEEK/POKE mode...")
                            dev_obj.aldb._read_write_mode = ReadWriteMode.PEEK_POKE
                            dev_obj.aldb._status = ALDBStatus.LOADED
                            _, failed = await dev_obj.aldb.async_write(force=True)
                    if failed:
                        dev_obj.aldb.clear_pending()
                        logger.error(f"Hardware write failed on {owner_addr} while purging ghost {clean_target}.")
                        hardware_failed_devices.append(f"{owner_addr} (Write failed)")
                        continue
                    logger.info(f"Hardware EEPROM write succeeded on {owner_addr}: {modified_slots} slot(s) deleted.")
                    await insteon_manager.refresh_device_db_delta_after_write(owner_addr, snapshot)
                    hardware_success_devices.append(owner_addr)
                else:
                    hardware_success_devices.append(owner_addr)

        # 1. Purge from PLM snapshot
        if not live_hw or plm_addr in hardware_success_devices:
            for rec in plm.get("records", []):
                if rec.get("in_use") and rec.get("target_address", "").upper() == clean_target:
                    rec["in_use"] = False
                    rec["raw_mode"] = rec.get("raw_mode", "").replace(" (Deleted)", "") + " (Deleted)"
                    purged_count += 1
                    logger.info(f"Purged PLM slot {rec.get('mem_addr')} (Group {rec.get('group')}) targeting ghost {clean_target}")
                    if plm_addr not in modified_devices:
                        modified_devices.append(plm_addr)

        # 2. Purge from peer devices in snapshot
        failed_addrs = [f.split()[0].upper() for f in hardware_failed_devices]
        for dev in snapshot.get("devices", []):
            dev_addr = dev.get("address", "").upper()
            is_hw_failed = live_hw and dev_addr in failed_addrs
            for rec in dev.get("records", []):
                if rec.get("in_use") and rec.get("target_address", "").upper() == clean_target:
                    rec["in_use"] = False
                    rec["raw_mode"] = rec.get("raw_mode", "").replace(" (Deleted)", "") + " (Deleted)"
                    purged_count += 1
                    note = " (snapshot purged; hardware EEPROM write timed out)" if is_hw_failed else ""
                    logger.info(f"Purged slot {rec.get('mem_addr')} on {dev_addr} (Group {rec.get('group')}) targeting ghost {clean_target}{note}")
                    if dev_addr not in modified_devices:
                        modified_devices.append(dev_addr)

        # 3. Remove any dead/failed 0-record stub for clean_target from snapshot["devices"]
        snapshot["devices"] = [
            d for d in snapshot.get("devices", [])
            if not (d.get("address", "").upper() == clean_target and not insteon_manager.is_active_snapshot_device(d))
        ]

        self._save_snapshot(snapshot)

        msg = f"Purged {purged_count} orphan references to {clean_target} across {len(modified_devices)} devices."
        if hardware_failed_devices:
            msg += f" Note: {len(hardware_failed_devices)} device(s) could not be written on physical hardware: {', '.join(hardware_failed_devices)} (to sync switch EEPROM, hold Set button on switch until it beeps and re-run)."

        logger.info(msg)

        return GenericResponse(
            success=True,
            message=msg,
            data={"target": clean_target, "records_purged": purged_count, "devices_affected": modified_devices, "skipped": hardware_failed_devices},
        )

    async def replace_device(self, req: DeviceReplaceRequest) -> GenericResponse:
        """Execute seamless 'Clone & Swap' replacement of old_address with new_address."""
        snapshot = self._load_snapshot()
        old_addr = req.old_address.strip().upper()
        new_addr = req.new_address.strip().upper()

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

        from app.insteon import insteon_manager
        live_hw = insteon_manager.is_connected and not insteon_manager._mock_mode and PYINSTEON_AVAILABLE

        if live_hw:
            old_addr_obj = Address(old_addr)
            new_addr_obj = Address(new_addr)

            if req.copy_links:
                ok_new, dev_new_obj, err_new = await insteon_manager.ensure_device_aldb_ready_for_write(new_addr, snapshot)
                if not ok_new or not dev_new_obj:
                    return GenericResponse(success=False, message=f"Cannot access replacement device {new_addr}: {err_new}")

                for rec in old_dev.get("records", []):
                    if rec.get("in_use") and not rec.get("high_water_mark"):
                        t_str = rec.get("target_address", "").upper()
                        if t_str == old_addr:
                            continue
                        dev_new_obj.aldb.add(
                            group=int(rec.get("group", 1)),
                            target=Address(t_str),
                            controller=bool(rec.get("is_controller", False)),
                            data1=int(rec.get("data1", 0)),
                            data2=int(rec.get("data2", 0)),
                            data3=int(rec.get("data3", 0)),
                        )
                from pyinsteon.constants import ALDBStatus
                dev_new_obj.aldb._status = ALDBStatus.LOADED
                _, failed_clone = await dev_new_obj.aldb.async_write(force=True)
                if failed_clone:
                    dev_new_obj.aldb.clear_pending()
                    return GenericResponse(success=False, message=f"Failed writing cloned links to new device {new_addr}.")
                await insteon_manager.refresh_device_db_delta_after_write(new_addr, snapshot)

            # Retarget peers & PLM on live hardware
            plm_addr = snapshot.get("plm", {}).get("address", "53.C1.42").upper()
            affected_owners = []
            if any(r.get("in_use") and r.get("target_address", "").upper() == old_addr for r in snapshot.get("plm", {}).get("records", [])):
                affected_owners.append(plm_addr)
            for d in snapshot.get("devices", []):
                da = d.get("address", "").upper()
                if da not in (old_addr, new_addr) and any(r.get("in_use") and r.get("target_address", "").upper() == old_addr for r in d.get("records", [])):
                    affected_owners.append(da)

            for owner_addr in affected_owners:
                ok_p, dev_p, err_p = await insteon_manager.ensure_device_aldb_ready_for_write(owner_addr, snapshot)
                if not ok_p or not dev_p:
                    return GenericResponse(success=False, message=f"Failed reaching peer {owner_addr} during replacement: {err_p}")
                for mem_k, rec in list(dev_p.aldb.items()):
                    if rec.is_in_use and str(rec.target).upper() == str(old_addr_obj).upper():
                        dev_p.aldb.modify(mem_addr=mem_k, target=new_addr_obj)
                dev_p.aldb._status = ALDBStatus.LOADED
                _, failed_p = await dev_p.aldb.async_write(force=True)
                if failed_p:
                    dev_p.aldb.clear_pending()
                    return GenericResponse(success=False, message=f"Failed retargeting links on peer {owner_addr}.")
                await insteon_manager.refresh_device_db_delta_after_write(owner_addr, snapshot)

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
        is_plm = clean_src == plm.get("address", "").upper()
        records_list = None

        if is_plm:
            records_list = plm.setdefault("records", [])
        else:
            for d in snapshot.get("devices", []):
                if d.get("address", "").upper() == clean_src:
                    records_list = d.setdefault("records", [])
                    break

        if records_list is None:
            return

        # 1. Update in place if an active record already matches (target, group, role)
        for r in records_list:
            if (
                r.get("in_use")
                and not r.get("high_water_mark")
                and r.get("target_address", "").upper() == clean_target
                and r.get("group") == group
                and bool(r.get("is_controller")) == is_controller
            ):
                r["data1"] = data1
                r["data2"] = data2
                r["data3"] = data3
                return

        # 2. Reuse the highest-address deleted/inactive slot (if any exists above the High-Water Mark)
        unused_slots = [
            r for r in records_list
            if not r.get("in_use", False) and not r.get("high_water_mark", False) and r.get("mem_addr")
        ]
        if unused_slots:
            try:
                unused_slots.sort(key=lambda x: int(str(x.get("mem_addr", "0x0000")), 16), reverse=True)
                reuse_slot = unused_slots[0]
                reuse_slot["target_address"] = clean_target
                reuse_slot["group"] = group
                reuse_slot["is_controller"] = is_controller
                reuse_slot["is_responder"] = is_responder
                reuse_slot["in_use"] = True
                reuse_slot["high_water_mark"] = False
                reuse_slot["data1"] = data1
                reuse_slot["data2"] = data2
                reuse_slot["data3"] = data3
                reuse_slot["raw_mode"] = "Controller" if is_controller else "Responder"
                return
            except Exception:
                pass

        # 3. If a High-Water Mark record exists, convert it into the new active record and push HWM down by 8 bytes
        hwm_slots = [r for r in records_list if r.get("high_water_mark", False) and r.get("mem_addr")]
        if hwm_slots and not is_plm:
            try:
                hwm_rec = hwm_slots[0]
                hwm_addr = int(str(hwm_rec.get("mem_addr", "0x0FFF")), 16)
                hwm_rec["target_address"] = clean_target
                hwm_rec["group"] = group
                hwm_rec["is_controller"] = is_controller
                hwm_rec["is_responder"] = is_responder
                hwm_rec["in_use"] = True
                hwm_rec["high_water_mark"] = False
                hwm_rec["data1"] = data1
                hwm_rec["data2"] = data2
                hwm_rec["data3"] = data3
                hwm_rec["raw_mode"] = "Controller" if is_controller else "Responder"

                next_hwm_addr = max(0x0000, hwm_addr - 8)
                records_list.append({
                    "mem_addr": f"0x{next_hwm_addr:04X}",
                    "target_address": "00.00.00",
                    "group": 0,
                    "is_controller": False,
                    "is_responder": True,
                    "in_use": False,
                    "high_water_mark": True,
                    "data1": 0,
                    "data2": 0,
                    "data3": 0,
                    "raw_mode": "Responder (Deleted)",
                })
                return
            except Exception:
                pass

        # 4. Fallback: allocate 8 bytes below minimum existing address
        default_top = 0x1FFF if is_plm else 0x0FFF
        if records_list:
            try:
                min_mem = min(int(str(r.get("mem_addr", f"0x{default_top:04X}")), 16) for r in records_list if r.get("mem_addr"))
                new_mem = max(0x0000, min_mem - 8)
            except Exception:
                new_mem = default_top - 8
        else:
            new_mem = default_top

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
                    logger.info(f"Deleted Controller link on {clean_src} slot {r.get('mem_addr')} (Grp {group} -> {clean_target})")
                elif not is_controller and (r.get("is_responder") or not r.get("is_controller")):
                    r["in_use"] = False
                    raw = r.get("raw_mode", "Responder")
                    r["raw_mode"] = raw.replace(" (Deleted)", "") + " (Deleted)"
                    deleted_count += 1
                    logger.info(f"Deleted Responder link on {clean_src} slot {r.get('mem_addr')} (Grp {group} <- {clean_target})")
        return deleted_count

    def audit_default_links(self) -> DefaultLinkAuditResponse:
        """Scan all active known devices against the PLM to detect missing or broken default PLM links."""
        from app.insteon import insteon_manager
        snapshot = self._load_snapshot()
        plm_addr = snapshot.get("plm", {}).get("address", "53.C1.42").upper()
        plm_records = [r for r in snapshot.get("plm", {}).get("records", []) if r.get("in_use") and not r.get("high_water_mark")]

        active_devices = [
            d for d in snapshot.get("devices", [])
            if d.get("address") and d.get("address", "").upper() != plm_addr and insteon_manager.is_active_snapshot_device(d)
        ]
        items: List[MissingDefaultLinkItem] = []

        for dev in active_devices:
            dev_addr = dev.get("address", "").upper()
            dev_name = dev.get("label") or dev.get("description", f"Device {dev_addr}")
            dev_type = dev.get("description", "Insteon Device")
            dev_records = [r for r in dev.get("records", []) if r.get("in_use") and not r.get("high_water_mark")]

            # 1. Device as Controller to PLM on Group 1 (Status reporting)
            dev_ctrl_group1 = any(
                r.get("target_address", "").upper() == plm_addr and r.get("is_controller") and r.get("group") == 1
                for r in dev_records
            )
            plm_resp_group1 = any(
                r.get("target_address", "").upper() == dev_addr and (r.get("is_responder") or not r.get("is_controller")) and r.get("group") == 1
                for r in plm_records
            )
            has_device_to_plm = dev_ctrl_group1 and plm_resp_group1

            # 2. PLM as Controller to Device on Group 0 (I2CS Auth & Direct Management) or Group 1 fallback
            plm_ctrl_group0 = any(
                r.get("target_address", "").upper() == dev_addr and r.get("is_controller") and r.get("group") in (0, 1)
                for r in plm_records
            )
            dev_resp_group0 = any(
                r.get("target_address", "").upper() == plm_addr and (r.get("is_responder") or not r.get("is_controller")) and r.get("group") in (0, 1)
                for r in dev_records
            )
            has_plm_to_device = plm_ctrl_group0 and dev_resp_group0

            if not has_device_to_plm or not has_plm_to_device:
                missing_d2p = not has_device_to_plm
                missing_p2d = not has_plm_to_device

                if missing_d2p and missing_p2d:
                    summary = "Missing Default PLM Link Pair (Group 0 Auth & Group 1 Status)"
                    rec = f"Device {dev_addr} ({dev_name}) is unlinked from PLM {plm_addr}. Recommend creating Group 0 PLM->Device management/I2CS auth link and Group 1 Device->PLM status link."
                elif missing_d2p:
                    summary = "Missing Device-to-PLM Status Link (Group 1)"
                    rec = f"Device {dev_addr} ({dev_name}) cannot report paddle presses to PLM {plm_addr}. Recommend creating Group 1 controller link in device and responder link in PLM."
                else:
                    summary = "Missing PLM-to-Device Management & I2CS Auth Link (Group 0)"
                    rec = f"Device {dev_addr} ({dev_name}) is missing its Group 0 PLM management / I2CS authorization link with {plm_addr}. Recommend creating Group 0 controller link in PLM and responder link in device."

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

        total_count = len(active_devices)
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
            # 1. PLM (Controller) <-> Device (Responder) on Group 0 (I2CS Auth & Direct Management)
            await self.create_reciprocal_link(
                ReciprocalLinkRequest(
                    controller_address=plm_addr,
                    responder_address=clean_addr,
                    group=0,
                    data1=3,
                    data2=21,
                    data3=158,
                )
            )
            # 2. Device (Controller) <-> PLM (Responder) on Group 1 (Status Reporting)
            await self.create_reciprocal_link(
                ReciprocalLinkRequest(
                    controller_address=clean_addr,
                    responder_address=plm_addr,
                    group=1,
                    data1=3,
                    data2=0,
                    data3=1,
                )
            )
            default_links_created = True

        msg = f"Device {clean_addr} ({req.description}) added successfully."
        if default_links_created:
            msg += f" Default Group 0 (I2CS Auth) and Group 1 (Status) links with PLM {plm_addr} created."

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
        - Dead device ghost references (including retired PLMs with 0-record FAILED stubs)
        - Missing default PLM links (Group 0 PLM->Device I2CS Auth & Group 1 Device->PLM Status)
        - Corrupted Group 0 links (excluding valid PLM Group 0 Management/I2CS Auth records)
        - Redundant duplicate records
        """
        from app.insteon import insteon_manager
        snapshot = self._load_snapshot()
        plm = snapshot.get("plm", {})
        plm_addr = plm.get("address", "53.C1.42").upper()
        raw_devices = snapshot.get("devices", [])

        # Only include active/reachable devices (exclude retired 0-record FAILED stubs like 2C.E7.EF & 39.F1.75)
        active_devices = [
            d for d in raw_devices
            if d.get("address") and insteon_manager.is_active_snapshot_device(d)
        ]

        all_dev_map = {d.get("address", "").upper(): d for d in active_devices}
        all_dev_map[plm_addr] = plm

        def get_device_name(addr: str) -> str:
            d = all_dev_map.get(addr.upper(), {})
            if addr.upper() == plm_addr:
                return "USB PowerLinc Modem (PLM)"
            return d.get("label") or d.get("description") or f"Device {addr}"

        def is_keypad(addr: str) -> bool:
            d = all_dev_map.get(addr.upper(), {})
            name = (d.get("label") or d.get("description") or "").lower()
            return "keypad" in name or "remote" in name or addr.upper() == "1C.2C.40"

        total_active_records = 0
        issues: List[NetworkIssueItem] = []
        active_links = []

        for owner_addr, dev in all_dev_map.items():
            recs = dev.get("records", [])
            seen_owner_keys: Dict[Any, Dict[str, Any]] = {}
            for r in recs:
                mem = r.get("mem_addr")
                in_use = r.get("in_use", False)
                hwm = r.get("high_water_mark", False)
                grp = r.get("group", 0)
                tgt = r.get("target_address", "").upper()
                is_ctrl = r.get("is_controller", False)
                is_resp = r.get("is_responder", False) or not is_ctrl
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

                # 2. Check Ghost Target (device not in active network, e.g., retired PLMs 2C.E7.EF / 39.F1.75)
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
                        summary=f"Ghost reference to retired/offline device {tgt} (Grp {grp})",
                        explanation=f"{owner_name} has an active {role} link in slot {mem} pointing to {tgt}, which is no longer active on the network. Active ghost links cause repeated powerline retries and delays.",
                        recommended_action="delete_record",
                        fix_label="🗑️ Delete Ghost Slot",
                        secondary_action="purge_ghost",
                        secondary_label=f"🧹 Purge All {tgt} Ghosts",
                        can_auto_fix=True,
                        data1=d1, data2=d2, data3=d3
                    ))
                    continue

                # 3. Check Group 0 records:
                # Valid Group 0 records are PLM (Controller) -> Device (Responder) and Device (Responder) <- PLM (Controller).
                # These provide I2CS Extended Command authorization and direct PLM device management.
                if grp == 0:
                    is_valid_plm_grp0 = (
                        (owner_addr == plm_addr and is_ctrl and tgt in all_dev_map and tgt != plm_addr)
                        or (tgt == plm_addr and is_resp and owner_addr in all_dev_map and owner_addr != plm_addr)
                    )
                    if not is_valid_plm_grp0:
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
                            summary=f"Invalid non-PLM Group 0 {role} record in {owner_name}",
                            explanation=f"Group 0 is reserved exclusively for PLM (Controller) -> Device (Responder) I2CS authorization links. A {role} record on Group 0 between {owner_name} and {tgt_name} is an invalid/orphaned handshake.",
                            recommended_action="delete_record",
                            fix_label="🗑️ Delete Invalid Group 0 Slot",
                            secondary_action=None,
                            secondary_label=None,
                            can_auto_fix=True,
                            data1=d1, data2=d2, data3=d3
                        ))
                        continue

                # 4. Check Redundant Duplicate
                # Note: On a Keypad Responder, different buttons on the same Keypad can respond to the same Group with different Data3 button numbers.
                dup_btn_discriminator = d3 if (is_resp and is_keypad(owner_addr)) else 0
                dup_key = (tgt, grp, role, dup_btn_discriminator)
                if dup_key in seen_owner_keys:
                    prev_entry = seen_owner_keys[dup_key]
                    # If the earlier Controller record had malformed data1=255 and this one has valid data1=3, flag the earlier malformed slot for deletion
                    if is_ctrl and owner_addr != plm_addr and prev_entry["data1"] == 255 and d1 != 255:
                        dup_mem = prev_entry["mem"]
                        dup_d1, dup_d2, dup_d3 = prev_entry["data1"], prev_entry["data2"], prev_entry["data3"]
                        prev_entry["mem"] = mem
                        prev_entry["data1"] = d1
                        prev_entry["data2"] = d2
                        prev_entry["data3"] = d3
                    else:
                        dup_mem = mem
                        dup_d1, dup_d2, dup_d3 = d1, d2, d3

                    issues.append(NetworkIssueItem(
                        id=f"dup_{owner_addr}_{dup_mem}",
                        issue_type="duplicate",
                        severity="warning",
                        owner_address=owner_addr,
                        owner_name=owner_name,
                        mem_addr=dup_mem,
                        target_address=tgt,
                        target_name=tgt_name,
                        group=grp,
                        role=role,
                        summary=f"Redundant duplicate {role} link on Group {grp}",
                        explanation=f"{owner_name} has multiple identical active {role} records for {tgt_name} on Group {grp}. Duplicate records cause redundant powerline cleanup traffic and waste EEPROM slots.",
                        recommended_action="delete_record",
                        fix_label="🗑️ Delete Duplicate Slot",
                        secondary_action=None,
                        secondary_label=None,
                        can_auto_fix=True,
                        data1=dup_d1, data2=dup_d2, data3=dup_d3
                    ))
                    continue

                link_entry = {
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
                }
                seen_owner_keys[dup_key] = link_entry

                # Valid Group 0 PLM links are audited for completeness in Step 6 (Missing Default PLM Links)
                if grp == 0:
                    continue

                active_links.append(link_entry)

        # 5. Check Half-Links (Missing reciprocal on target for Scene/Group >= 1)
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

                rec_action = "create_reciprocal"
                sec_action = "delete_record"
                sec_label = "🗑️ Delete Broken Slot"
                can_auto = True

                # When creating a missing Responder link from a Controller half-link, use standard 100% On-Level (255) & 0.5s Ramp (28),
                # NOT the Controller record's retry count (d1=3, d2=0)!
                fix_d1 = 255 if role == "Controller" else d1
                fix_d2 = 28 if role == "Controller" else d2
                fix_d3 = 1 if role == "Controller" else d3

                if is_kp and role == "Controller":
                    severity = "warning"
                    button_label = f"Button {chr(64 + grp)} (Key {grp})" if 1 <= grp <= 8 else f"Group {grp}"
                    summary = f"Keypad {button_label} Orphan Slot → {target_name}"
                    explanation = f"{owner_name} has an unverified controller slot for {target_name} on {button_label}, but {target_name} has NO matching responder link. If this keypad button is NOT supposed to control {target_name}, delete this slot. If you want {button_label} to control {target_name}, click 'Add Responder Link'."
                    fix_label = f"🗑️ Delete Stale Slot on {owner_name}"
                    rec_action = "delete_record"
                    sec_action = "create_reciprocal"
                    sec_label = f"⚡ Add Responder Link on {target_name}"
                    can_auto = False
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

                # Never offer to delete broken PLM links on Group 0 or 1; they must only be restored
                if (owner == plm_addr or target == plm_addr) and grp in (0, 1):
                    sec_action = None
                    sec_label = None

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
                    recommended_action=rec_action,
                    fix_label=fix_label,
                    secondary_action=sec_action,
                    secondary_label=sec_label,
                    can_auto_fix=can_auto,
                    data1=fix_d1, data2=fix_d2, data3=fix_d3
                ))

        # 6. Check Missing Default PLM Links (Group 0 PLM->Device I2CS Auth + Group 1 Device->PLM Status)
        plm_records = [r for r in plm.get("records", []) if r.get("in_use") and not r.get("high_water_mark")]
        for dev in active_devices:
            dev_addr = dev.get("address", "").upper()
            if dev_addr == plm_addr or not dev_addr:
                continue
            dev_name = get_device_name(dev_addr)
            dev_records = [r for r in dev.get("records", []) if r.get("in_use") and not r.get("high_water_mark")]

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
                    explanation=f"{dev_name} is missing its default Group 0 (I2CS Auth / Direct Control) or Group 1 (Status Reporting) link pair with the PLM.",
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

