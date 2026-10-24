"""Scene Management Service for Insteon network.

Provides atomic creation, deletion, fine-grained member preset adjustments
(brightness & ramp rate), and scene triggering without destructive full-scene rewrites.
"""

import json
import logging
import os
import asyncio
from typing import Dict, List, Optional, Tuple, Any

from app.config import settings
from app.models import (
    SceneResponse,
    SceneControllerModel,
    SceneMemberModel,
    SceneCreateRequest,
    SceneMemberUpdateRequest,
    SceneMemberAddRequest,
    ReciprocalLinkRequest,
    GenericResponse,
    ALDBRecordResponse,
)

logger = logging.getLogger("insteon_scenes")

try:
    import pyinsteon
    from pyinsteon.address import Address
    from pyinsteon.constants import ResponseStatus, ALDBStatus, ReadWriteMode
    from pyinsteon.handlers.send_all_link_on import SendAllLinkOnCommandHandler
    from pyinsteon.handlers.send_all_link_off import SendAllLinkOffCommandHandler
    PYINSTEON_AVAILABLE = True
except ImportError:
    PYINSTEON_AVAILABLE = False


class SceneService:
    """Manages Insteon Scenes (All-Link Groups) across controllers and responders."""

    def __init__(self):
        self._scene_names_file = os.path.join(settings.cache_dir, "insteon_scenes.json")
        self._custom_names: Dict[int, str] = {}
        self._load_custom_names()

    def _load_custom_names(self):
        """Load friendly scene names from disk if present."""
        candidates = [
            self._scene_names_file,
            "insteon_scenes.json",
            os.path.join("backend", "insteon_scenes.json"),
            os.path.join("data", "insteon_scenes.json"),
            os.path.join("backend", "data", "insteon_scenes.json"),
        ]
        for path in candidates:
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        self._custom_names = {int(k): str(v) for k, v in data.items()}
                        return
                except Exception as ex:
                    logger.warning(f"Error loading scene names from {path}: {ex}")

    def _save_custom_names(self):
        """Persist friendly scene names to disk."""
        try:
            os.makedirs(settings.cache_dir, exist_ok=True)
            with open(self._scene_names_file, "w", encoding="utf-8") as f:
                json.dump({str(k): v for k, v in self._custom_names.items()}, f, indent=2)
        except Exception as ex:
            logger.error(f"Failed saving scene names to {self._scene_names_file}: {ex}")

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
            logger.error(f"Error writing snapshot to {path}: {ex}")

    def get_all_scenes(self) -> List[SceneResponse]:
        """Aggregate all scenes in the network from PLM and device ALDBs."""
        snapshot = self._load_snapshot()
        if not snapshot:
            return []

        plm = snapshot.get("plm", {})
        plm_addr = plm.get("address", "53.C1.42")
        devices = snapshot.get("devices", [])

        # Build address -> friendly label lookup
        labels: Dict[str, str] = {plm_addr: "USB PLM Modem"}
        for dev in devices:
            if dev.get("address"):
                labels[dev["address"]] = dev.get("label") or dev.get("description", dev["address"])

        # group -> { controllers: [], members: [] }
        scenes_by_group: Dict[int, Dict[str, Any]] = {}

        # 1. Parse PLM Controller records for scenes (group >= 1)
        for rec in plm.get("records", []):
            if not rec.get("in_use"):
                continue
            grp = rec.get("group", 0)
            if grp < 1:
                continue

            scenes_by_group.setdefault(grp, {"controllers": {}, "members": {}})
            if rec.get("is_controller"):
                ctrl_key = (plm_addr, 0)
                scenes_by_group[grp]["controllers"][ctrl_key] = SceneControllerModel(
                    address=plm_addr,
                    label=labels.get(plm_addr, "PLM"),
                    button_group=0,
                    is_plm=True,
                )

        # 2. Parse peer device records (both Controller keypads/switches and Responder dimmers/relays)
        for dev in devices:
            dev_addr = dev.get("address", "")
            if not dev_addr:
                continue
            dev_label = labels.get(dev_addr, dev_addr)

            for rec in dev.get("records", []):
                if not rec.get("in_use"):
                    continue
                grp = rec.get("group", 0)
                if grp < 1:
                    continue

                scenes_by_group.setdefault(grp, {"controllers": {}, "members": {}})

                # If this device is a Controller for this scene (e.g. Mini Keypad button)
                if rec.get("is_controller"):
                    btn = rec.get("data3", 0) or 0
                    ctrl_key = (dev_addr, btn)
                    scenes_by_group[grp]["controllers"][ctrl_key] = SceneControllerModel(
                        address=dev_addr,
                        label=dev_label,
                        button_group=btn,
                        is_plm=False,
                    )

                # If this device is a Responder for this scene (e.g. Dimmer or Lamp)
                if rec.get("is_responder"):
                    scenes_by_group[grp]["members"][dev_addr] = SceneMemberModel(
                        address=dev_addr,
                        label=dev_label,
                        on_level=int(rec.get("data1", 255)),
                        ramp_rate=int(rec.get("data2", 28)),
                        button_group=int(rec.get("data3", 1) or 1),
                        mem_addr=rec.get("mem_addr"),
                    )

        # Also include any scenes with custom names in self._custom_names (e.g. freshly created empty scenes)
        for custom_grp in self._custom_names.keys():
            if custom_grp not in scenes_by_group:
                scenes_by_group[custom_grp] = {"controllers": {}, "members": {}}

        results: List[SceneResponse] = []
        for grp in sorted(scenes_by_group.keys()):
            data = scenes_by_group[grp]
            ctrl_list = list(data["controllers"].values())
            memb_list = list(data["members"].values())

            # Only expose scenes that have at least one controller, one member, or a registered custom name
            if not ctrl_list and not memb_list and grp not in self._custom_names:
                continue

            default_name = f"Insteon Scene {grp}"
            name = self._custom_names.get(grp) or default_name

            has_plm = any(c.is_plm for c in ctrl_list)
            results.append(
                SceneResponse(
                    group=grp,
                    name=name,
                    controllers=ctrl_list,
                    members=memb_list,
                    is_plm_controlled=has_plm,
                )
            )

        return results

    def get_scene(self, group: int) -> Optional[SceneResponse]:
        """Get details for a single scene by group number."""
        for sc in self.get_all_scenes():
            if sc.group == group:
                return sc
        return None

    async def update_scene_member_preset(
        self, group: int, address: str, update: SceneMemberUpdateRequest
    ) -> SceneMemberModel:
        """Surgically adjust On-Level and/or Ramp-Rate for a single member in a scene.
        
        Touches ONLY this member's EEPROM slot without modifying other scene members.
        Requires active hardware session and verifies silicon before committing to database.
        """
        snapshot = self._load_snapshot()
        if not snapshot:
            raise RuntimeError("No network snapshot available to locate member record.")

        clean_addr = address.strip().upper()
        target_dev = None
        matching_recs = []
        for dev in snapshot.get("devices", []):
            if dev.get("address", "").upper() == clean_addr:
                target_dev = dev
                for rec in dev.get("records", []):
                    if rec.get("in_use") and rec.get("group") == group and rec.get("is_responder"):
                        matching_recs.append(rec)
                break

        if not target_dev or not matching_recs:
            raise ValueError(f"Device {address} is not a responder in Scene {group}.")

        target_rec = matching_recs[0]
        new_data1 = int(update.on_level) if update.on_level is not None else int(target_rec.get("data1", 255))
        new_data2 = int(update.ramp_rate) if update.ramp_rate is not None else int(target_rec.get("data2", 28))

        from app.insteon import insteon_manager
        live_hw = insteon_manager.is_connected and not insteon_manager._mock_mode and PYINSTEON_AVAILABLE

        # STRICT GUARD: Never perform offline fantasy mutations in production
        if not settings.is_simulator and not live_hw:
            raise RuntimeError(
                f"Cannot update preset on {clean_addr}: No active hardware session lock. "
                f"Please acquire a session lock first so openHAB is paused during the EEPROM write."
            )

        # 1. Hardware update and silicon read-back verification
        if live_hw:
            ok, dev_obj, err_msg = await insteon_manager.ensure_device_aldb_ready_for_write(clean_addr, snapshot)
            if not ok or not dev_obj:
                raise RuntimeError(f"Hardware write pre-check failed for {clean_addr}: {err_msg}")

            dev_obj.aldb._status = ALDBStatus.LOADED
            modified_count = 0
            for r in matching_recs:
                mem_int = int(r["mem_addr"], 16) if isinstance(r["mem_addr"], str) else int(r["mem_addr"])
                rec_obj = dev_obj.aldb.get(mem_int)
                if not rec_obj:
                    # Fallback match by group and controller status
                    t_addr = Address(r.get("target_address", "00.00.00"))
                    matches = list(dev_obj.aldb.find(target=t_addr, group=group, is_controller=False, in_use=True))
                    if matches:
                        rec_obj = matches[0]
                        mem_int = rec_obj.mem_addr
                        r["mem_addr"] = f"0x{mem_int:04X}"

                if rec_obj:
                    dev_obj.aldb.modify(
                        mem_addr=mem_int,
                        data1=new_data1,
                        data2=new_data2,
                    )
                    modified_count += 1

            if modified_count == 0:
                raise RuntimeError(f"Could not locate matching ALDB record on {clean_addr} to modify.")

            logger.info(f"Writing updated preset to {clean_addr} (Level={new_data1}, Ramp={new_data2})...")
            done, failed = await dev_obj.aldb.async_write(force=True)
            if failed and getattr(dev_obj, "engine_version", None) == EngineVersion.I1 and getattr(dev_obj.aldb, "_read_write_mode", None) != ReadWriteMode.PEEK_POKE:
                logger.info(f"Standard extended write failed on legacy I1 device {clean_addr}; retrying with PEEK/POKE mode...")
                dev_obj.aldb._read_write_mode = ReadWriteMode.PEEK_POKE
                dev_obj.aldb._status = ALDBStatus.LOADED
                done, failed = await dev_obj.aldb.async_write(force=True)

            from app.insteon import bus_pacer
            await bus_pacer.mark_write_completed()

            if failed:
                dev_obj.aldb.clear_pending()
                raise RuntimeError(f"Hardware EEPROM write to {clean_addr} failed. Snapshot was NOT modified.")

            # Allow EEPROM charge-pump burn cycle to complete before silicon read-back
            await bus_pacer.wait_post_write_settle()

            # MANDATORY TWO-PHASE SILICON READ-BACK VERIFICATION
            for r in matching_recs:
                mem_int = int(r["mem_addr"], 16) if isinstance(r["mem_addr"], str) else int(r["mem_addr"])
                tgt_str = str(r.get("target_address", "")).upper()
                v_ok, v_msg, _ = await insteon_manager.verify_hardware_record(
                    address=clean_addr,
                    mem_addr=mem_int,
                    expected_target=tgt_str,
                    expected_group=group,
                    expected_controller=False,
                    expected_in_use=True,
                    expected_data1=new_data1,
                    expected_data2=new_data2,
                )
                if not v_ok:
                    dev_obj.aldb.clear_pending()
                    logger.error(f"Hardware live silicon verification failed for preset on {clean_addr}: {v_msg}")
                    raise RuntimeError(
                        f"Hardware EEPROM verification failed on {clean_addr} ({v_msg}). "
                        f"Physical EEPROM does not match requested preset. Snapshot database was NOT modified to maintain hardware truth."
                    )
                logger.info(f"Hardware EEPROM write verified on {clean_addr}: Slot 0x{mem_int:04X} confirmed on silicon (Level={new_data1}, Ramp={new_data2}).")

            await insteon_manager.refresh_device_db_delta_after_write(clean_addr, snapshot)

        # 2. Persist to snapshot file ONLY AFTER hardware silicon verification succeeds (or if in simulator)
        for r in matching_recs:
            r["data1"] = new_data1
            r["data2"] = new_data2
        self._save_snapshot(snapshot)

        return SceneMemberModel(
            address=clean_addr,
            label=target_dev.get("label", clean_addr),
            on_level=new_data1,
            ramp_rate=new_data2,
            button_group=target_rec.get("data3", 1),
            mem_addr=target_rec.get("mem_addr"),
        )

    async def trigger_scene(self, group: int, action: str = "on") -> bool:
        """Trigger an Insteon scene live over the PLM."""
        from app.insteon import insteon_manager
        if not insteon_manager.is_connected or not PYINSTEON_AVAILABLE:
            logger.info(f"[Mock] Triggered Scene {group} action '{action}'")
            return True

        action_clean = action.lower().strip()
        if action_clean in ("on", "fast_on"):
            cmd = SendAllLinkOnCommandHandler()
            logger.info(f"Broadcasting All-Link ON to Scene {group} via PLM...")
            res = await cmd.async_send(group=group)
            return res == ResponseStatus.SUCCESS
        elif action_clean in ("off", "fast_off"):
            cmd = SendAllLinkOffCommandHandler()
            logger.info(f"Broadcasting All-Link OFF to Scene {group} via PLM...")
            res = await cmd.async_send(group=group)
            return res == ResponseStatus.SUCCESS
        else:
            raise ValueError(f"Unknown scene action '{action}'. Use 'on' or 'off'.")

    async def add_scene_member(self, group: int, req: SceneMemberAddRequest) -> GenericResponse:
        """Add a device as a responder to a scene, establishing reciprocal links with the scene controller(s)."""
        from app.services.transaction_service import transaction_service
        snapshot = self._load_snapshot()
        if not snapshot:
            return GenericResponse(success=False, message="No network snapshot available.")

        clean_resp = req.address.strip().upper()
        plm_addr = snapshot.get("plm", {}).get("address", "53.C1.42").upper()

        if clean_resp == plm_addr:
            return GenericResponse(success=False, message=f"The PLM modem ({clean_resp}) cannot be added as a scene responder.")

        target_dev = next((d for d in snapshot.get("devices", []) if d.get("address", "").upper() == clean_resp), None)
        if not target_dev:
            return GenericResponse(success=False, message=f"Device {clean_resp} not found in network.")

        # Check if already a member in this scene group
        existing_recs = [
            r for r in target_dev.get("records", [])
            if r.get("in_use") and r.get("group") == group and r.get("is_responder")
        ]
        if existing_recs:
            return GenericResponse(
                success=False,
                message=f"Device {clean_resp} is already a member of Scene {group}. Use preset tuning to adjust its brightness.",
            )

        # Determine which controller(s) this member should respond to
        controllers_to_link: List[str] = []
        if req.controller_address:
            controllers_to_link.append(req.controller_address.strip().upper())
        else:
            scene = self.get_scene(group=group)
            if scene and scene.controllers:
                for ctrl in scene.controllers:
                    c_addr = ctrl.address.strip().upper()
                    if c_addr not in controllers_to_link:
                        controllers_to_link.append(c_addr)
            if not controllers_to_link:
                controllers_to_link.append(plm_addr)

        created_count = 0
        errors = []
        for ctrl_addr in controllers_to_link:
            res = await transaction_service.create_reciprocal_link(
                ReciprocalLinkRequest(
                    controller_address=ctrl_addr,
                    responder_address=clean_resp,
                    group=group,
                    data1=req.on_level,
                    data2=req.ramp_rate,
                    data3=req.button_group or 1,
                )
            )
            if res.success:
                created_count += 1
            else:
                errors.append(f"{ctrl_addr}: {res.message}")

        dev_label = target_dev.get("label", clean_resp)
        if created_count > 0:
            msg = f"Added {dev_label} ({clean_resp}) to Scene {group} with {created_count} controller link(s)."
            if errors:
                msg += f" (Partial notices: {'; '.join(errors)})"
            return GenericResponse(success=True, message=msg)
        else:
            return GenericResponse(success=False, message=f"Failed adding {clean_resp} to Scene {group}: {'; '.join(errors)}")

    async def remove_scene_member(self, group: int, address: str) -> GenericResponse:
        """Remove a responder device from a scene, deleting reciprocal links with all scene controllers."""
        from app.services.transaction_service import transaction_service
        snapshot = self._load_snapshot()
        if not snapshot:
            return GenericResponse(success=False, message="No network snapshot available.")

        clean_resp = address.strip().upper()
        target_dev = next((d for d in snapshot.get("devices", []) if d.get("address", "").upper() == clean_resp), None)
        if not target_dev:
            return GenericResponse(success=False, message=f"Device {clean_resp} not found in network.")

        # Find all controllers linked to this device for this scene
        controllers_to_unlink = set()
        for rec in target_dev.get("records", []):
            if rec.get("in_use") and rec.get("group") == group and rec.get("is_responder"):
                tgt = rec.get("target_address", "").strip().upper()
                if tgt:
                    controllers_to_unlink.add(tgt)

        # Also check if PLM has controller records for this device on this group
        plm = snapshot.get("plm", {})
        plm_addr = plm.get("address", "53.C1.42").upper()
        for rec in plm.get("records", []):
            if rec.get("in_use") and rec.get("group") == group and rec.get("is_controller"):
                if rec.get("target_address", "").strip().upper() == clean_resp:
                    controllers_to_unlink.add(plm_addr)

        if not controllers_to_unlink:
            # Fallback: check all scene controllers
            scene = self.get_scene(group=group)
            if scene:
                for c in scene.controllers:
                    controllers_to_unlink.add(c.address.strip().upper())

        if not controllers_to_unlink:
            return GenericResponse(success=False, message=f"No active controller links found for {clean_resp} in Scene {group}.")

        deleted_count = 0
        errors = []
        for ctrl_addr in controllers_to_unlink:
            res = await transaction_service.delete_reciprocal_link(
                ctrl_addr=ctrl_addr,
                resp_addr=clean_resp,
                group=group,
            )
            if res.success:
                deleted_count += 1
            else:
                errors.append(f"{ctrl_addr}: {res.message}")

        dev_label = target_dev.get("label", clean_resp)
        if deleted_count > 0:
            msg = f"Removed {dev_label} ({clean_resp}) from Scene {group} ({deleted_count} link pair(s) deleted)."
            if errors:
                msg += f" (Partial notices: {'; '.join(errors)})"
            return GenericResponse(success=True, message=msg)
        else:
            return GenericResponse(success=False, message=f"Failed removing {clean_resp} from Scene {group}: {'; '.join(errors)}")

    async def create_scene(self, req: SceneCreateRequest) -> GenericResponse:
        """Create a new Insteon scene with friendly name and optional initial members."""
        snapshot = self._load_snapshot()
        plm = snapshot.get("plm", {})
        plm_addr = plm.get("address", "53.C1.42").upper()

        # 1. Determine and validate Group number
        group = req.group
        existing_scenes = self.get_all_scenes()
        existing_groups = {s.group for s in existing_scenes}

        if group is not None:
            if group in (0, 1):
                return GenericResponse(
                    success=False,
                    message="Groups 0 and 1 are reserved for system direct management and status reporting. Please choose a group between 2 and 250."
                )
            if group < 1 or group > 255:
                return GenericResponse(success=False, message="Scene group number must be between 2 and 250.")
            if group in existing_groups:
                existing_name = next((s.name for s in existing_scenes if s.group == group), f"Group {group}")
                return GenericResponse(
                    success=False,
                    message=f"Scene Group {group} already exists ('{existing_name}'). Please choose a different group number or edit the existing scene."
                )
        else:
            # Auto-allocate next free group number (standard custom scenes range from 20 to 250)
            candidate = 20
            while candidate in existing_groups or candidate in self._custom_names:
                candidate += 1
                if candidate > 250:
                    candidate = 2
                    while candidate in existing_groups or candidate in self._custom_names:
                        candidate += 1
                        if candidate >= 20:
                            return GenericResponse(success=False, message="No available Insteon group numbers between 2 and 250.")
                    break
            group = candidate

        # 2. Save friendly name
        scene_name = req.name.strip() or f"Insteon Scene {group}"
        self._custom_names[group] = scene_name
        self._save_custom_names()

        # 3. Add initial members if provided
        created_members = 0
        member_errors = []
        if req.members:
            for m in req.members:
                add_res = await self.add_scene_member(
                    group=group,
                    req=SceneMemberAddRequest(
                        address=m.address,
                        on_level=m.on_level,
                        ramp_rate=m.ramp_rate,
                        button_group=m.button_group or 1,
                    )
                )
                if add_res.success:
                    created_members += 1
                else:
                    member_errors.append(f"{m.address}: {add_res.message}")

        msg = f"Scene {group} ('{scene_name}') created successfully."
        if created_members > 0:
            msg += f" Added {created_members} initial member device(s)."
        if member_errors:
            msg += f" (Notices: {'; '.join(member_errors)})"

        logger.info(msg)
        return GenericResponse(
            success=True,
            message=msg,
            data={"group": group, "name": scene_name, "members_added": created_members}
        )

    async def delete_scene(self, group: int) -> GenericResponse:
        """Completely delete an Insteon scene, removing all controller and responder links across the network."""
        from app.services.transaction_service import transaction_service
        from app.insteon import insteon_manager

        if group in (0, 1):
            return GenericResponse(
                success=False,
                message="Protected system groups (Group 0 and Group 1) cannot be deleted."
            )

        snapshot = self._load_snapshot()
        if not snapshot:
            return GenericResponse(success=False, message="No network snapshot available.")

        plm = snapshot.get("plm", {})
        plm_addr = plm.get("address", "53.C1.42").upper()

        live_hw = insteon_manager.is_connected and not insteon_manager._mock_mode and PYINSTEON_AVAILABLE
        if not settings.is_simulator and not live_hw:
            return GenericResponse(
                success=False,
                message="Active hardware session lock required before deleting scenes from hardware. Call POST /session/acquire first."
            )

        scene_name = self._custom_names.get(group, f"Scene {group}")

        # 1. Find all responder devices and their member records in this scene
        members_to_remove = set()
        for dev in snapshot.get("devices", []):
            d_addr = dev.get("address", "").upper()
            if any(r.get("in_use") and r.get("group") == group and r.get("is_responder") for r in dev.get("records", [])):
                members_to_remove.add(d_addr)

        removed_members = 0
        member_errors = []
        for m_addr in members_to_remove:
            rem_res = await self.remove_scene_member(group=group, address=m_addr)
            if rem_res.success:
                removed_members += 1
            else:
                member_errors.append(f"{m_addr}: {rem_res.message}")

        # 2. Clean up any remaining dangling controller/responder records on the PLM for this group
        plm_cleaned = 0
        snapshot = self._load_snapshot()
        plm = snapshot.get("plm", {})
        plm_records_to_purge = [
            r for r in plm.get("records", [])
            if r.get("in_use") and r.get("group") == group
        ]
        for r in plm_records_to_purge:
            mem = r.get("mem_addr")
            del_res = await transaction_service.delete_device_aldb_record(plm_addr, mem)
            if del_res.success:
                plm_cleaned += 1

        # 3. Clean up any remaining dangling controller/responder records on peer devices (e.g. Keypads / Switches)
        peer_cleaned = 0
        snapshot = self._load_snapshot()
        for dev in snapshot.get("devices", []):
            d_addr = dev.get("address", "").upper()
            peer_recs = [
                r for r in dev.get("records", [])
                if r.get("in_use") and r.get("group") == group
            ]
            for r in peer_recs:
                mem = r.get("mem_addr")
                del_res = await transaction_service.delete_device_aldb_record(d_addr, mem)
                if del_res.success:
                    peer_cleaned += 1

        # 4. Remove friendly name from custom names
        if group in self._custom_names:
            del self._custom_names[group]
            self._save_custom_names()

        total_cleaned = removed_members + plm_cleaned + peer_cleaned
        msg = f"Scene {group} ('{scene_name}') successfully deleted."
        msg += f" Removed {removed_members} responder device(s), {plm_cleaned} PLM slot(s), and {peer_cleaned} peer controller slot(s)."
        if member_errors:
            msg += f" (Notices: {'; '.join(member_errors)})"

        logger.info(msg)
        return GenericResponse(
            success=True,
            message=msg,
            data={"group": group, "name": scene_name, "total_records_cleaned": total_cleaned}
        )

    def set_scene_name(self, group: int, name: str):
        """Set or update the friendly name for a scene."""
        self._custom_names[group] = name.strip()
        self._save_custom_names()


scene_service = SceneService()
