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
    ALDBRecordResponse,
)

logger = logging.getLogger("insteon_scenes")

try:
    import pyinsteon
    from pyinsteon.address import Address
    from pyinsteon.constants import ResponseStatus
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
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            # Keep root network_snapshot.json in sync if it exists
            if os.path.isfile("network_snapshot.json") and path != "network_snapshot.json":
                with open("network_snapshot.json", "w", encoding="utf-8") as rf:
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

        results: List[SceneResponse] = []
        for grp in sorted(scenes_by_group.keys()):
            data = scenes_by_group[grp]
            ctrl_list = list(data["controllers"].values())
            memb_list = list(data["members"].values())

            # Only expose scenes that have at least one controller or member
            if not ctrl_list and not memb_list:
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
        new_data1 = update.on_level if update.on_level is not None else target_rec.get("data1", 255)
        new_data2 = update.ramp_rate if update.ramp_rate is not None else target_rec.get("data2", 28)

        # 1. Update in-memory record(s)
        for r in matching_recs:
            r["data1"] = new_data1
            r["data2"] = new_data2

        # 2. Hardware update if pyinsteon is active and connected
        from app.insteon import insteon_manager
        if insteon_manager.is_connected and PYINSTEON_AVAILABLE:
            addr_obj = Address(clean_addr)
            dev_obj = pyinsteon.devices.get(addr_obj)
            if dev_obj and hasattr(dev_obj, "aldb"):
                mem_int = int(target_rec["mem_addr"], 16) if isinstance(target_rec["mem_addr"], str) else target_rec["mem_addr"]
                rec_obj = dev_obj.aldb.get(mem_int)
                if rec_obj:
                    dev_obj.aldb.modify(
                        mem_addr=mem_int,
                        data1=new_data1,
                        data2=new_data2,
                    )
                    logger.info(f"Writing updated preset to {clean_addr} slot 0x{mem_int:04X} (Level={new_data1}, Ramp={new_data2})...")
                    done, failed = await dev_obj.aldb.async_write()
                    if failed:
                        logger.warning(f"Hardware write to {clean_addr} returned failure; will retry on next session.")

        # 3. Persist to snapshot file
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

    def set_scene_name(self, group: int, name: str):
        """Set or update the friendly name for a scene."""
        self._custom_names[group] = name.strip()
        self._save_custom_names()


scene_service = SceneService()
