#!/usr/bin/env python3
"""
Insteon Network Deep State Snapshot & ALDB Audit Utility (v2.0 - Dual-Band Optimized).

Safely queries the local Insteon USB PLM and line-powered Dual-Band devices to extract
a complete topology database (including inter-device cross links and scene links).

Key Protocol Optimizations:
1. Suppresses pyinsteon's background `async_inspect_device` storm (`delay_inspection = True`
   and unsubscribing `ADD_DEFAULT_LINKS`) so background Operating Flag / Extended Property
   queries never collide with ALDB reads.
2. Uses Insteon Standard Direct `STATUS_REQUEST` (0x19) to read each device's 8-bit
   Database Delta (`db_delta`) in ~0.3s. If `db_delta` matches the previous snapshot,
   skips the heavy Extended ALDB read entirely.
3. Fixes pyinsteon's `ALDBReadManager._read_all` High Water Mark (HWM) hang: exits the
   bulk stream immediately upon receiving the HWM record (saving 10s+ per dropped packet)
   and transitions straight to surgical single-record (`num_recs=1`) gap-filling.
4. Preserves partial ALDB records across retries (`refresh=False`) and falls back to a
   paced record-by-record walk (`num_recs=1`) when Dual-Band simulcast repeaters collide
   with unthrottled (`num_recs=0`) burst streams.
5. Executes an automatic Pass 2 Straggler Recovery pass for any devices that experienced
   transient powerline noise during Pass 1.
"""

import sys
import os
import time
import json
import signal
import asyncio
import logging
import argparse
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

# Tune pyinsteon serial write pacing BEFORE importing pyinsteon (default 0.5s -> 0.30s)
os.environ.setdefault("PYINSTEON_WRITE_WAIT", "0.30")

import httpx

# Configure dual logging: clean terminal + detailed diagnostic log file
LOG_FILENAME = "insteon_snapshot.log"
file_handler = logging.FileHandler(LOG_FILENAME, mode="w", encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter("%(message)s"))

logger = logging.getLogger("insteon_snapshot")
logger.setLevel(logging.DEBUG)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Try importing pyinsteon
try:
    import pyinsteon
    from pyinsteon.constants import ALDBStatus, EngineVersion, ReadWriteMode, ResponseStatus
    from pyinsteon.aldb.aldb import ALDB
    from pyinsteon.aldb.aldb_record import ALDBRecord
    from pyinsteon.data_types.all_link_record_flags import AllLinkRecordFlags
    from pyinsteon.handlers.to_device.status_request import StatusRequestCommand
    from pyinsteon.managers import aldb_read_manager
    from pyinsteon.managers.device_health import get_health
    from pyinsteon.managers.link_manager.default_links import async_add_default_links
    from pyinsteon.topics import ADD_DEFAULT_LINKS
    from pyinsteon.utils import unsubscribe_topic
    import async_timeout

    PYINSTEON_AVAILABLE = True
except ImportError:
    PYINSTEON_AVAILABLE = False

# Automatically load defaults from .env via app.config if available
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
try:
    from app.config import settings
    DEFAULT_PORT = settings.insteon_port
    DEFAULT_BAUD = settings.insteon_baud
    DEFAULT_OH_URL = settings.openhab_url
    DEFAULT_OH_THING = settings.openhab_thing_uid
    DEFAULT_OH_TOKEN = settings.openhab_token
    DEFAULT_CACHE_DIR = settings.cache_dir
except Exception:
    DEFAULT_PORT = "/dev/ttyUSB0"
    DEFAULT_BAUD = 19200
    DEFAULT_OH_URL = "http://localhost:8080"
    DEFAULT_OH_THING = "insteon:network:local"
    DEFAULT_OH_TOKEN = ""
    DEFAULT_CACHE_DIR = "./insteon_cache"


def apply_pyinsteon_protocol_optimizations():
    """
    Patch pyinsteon's ALDBReadManager and DeviceManager for fast, collision-free
    Dual-Band ALDB reading.
    """
    if not PYINSTEON_AVAILABLE:
        return

    # 1. Prevent set_id() / device discovery from launching background async_inspect_device()
    #    tasks (which send competing Operating Flag, Extended Property, and Default Link writes).
    pyinsteon.devices.delay_inspection = True
    try:
        unsubscribe_topic(async_add_default_links, ADD_DEFAULT_LINKS)
    except Exception:
        pass

    # 2. Tune ALDBReadManager timers:
    #    - Only do 1 bulk stream (num_recs=0) before switching to surgical num_recs=1 gap fill.
    #    - Reduce end-of-stream idle timeout from 10.0s to 3.5s.
    aldb_read_manager.RETRIES_ALL_MAX = 1
    aldb_read_manager.RETRIES_ONE_MAX = 3
    aldb_read_manager.TIMER_RECORD = 3.5

    # 3. Patch ALDBReadManager._read_all to exit immediately as soon as the High Water Mark
    #    record arrives, eliminating the 10-second phantom timeout when an earlier record dropped.
    async def _optimized_read_all(self):
        retries = aldb_read_manager.RETRIES_ALL_MAX
        mem_addr = 0
        while retries and self._continue:
            if not get_health(self._address).can_continue_operation():
                return
            response = await self._read_handler.async_send(mem_addr=mem_addr, num_recs=0)
            if response in [
                ResponseStatus.DIRECT_NAK_ALDB,
                ResponseStatus.DIRECT_NAK_INVALID_COMMAND,
                ResponseStatus.DIRECT_NAK_CHECK_SUM,
                ResponseStatus.DIRECT_NAK_PRE_NAK,
            ]:
                self._last_nak_response = response
                logger.warning(
                    f"    Device {self._address} rejected Extended 0x2F ALDB Read with {response.name} (0x{int(response):02X})."
                )
                return
            try:
                while self._continue:
                    async with async_timeout.timeout(aldb_read_manager.TIMER_RECORD):
                        record = await self._record_queue.get()
                        if record is None:
                            return
                        if aldb_read_manager.is_erased_record(record):
                            self._hit_erased = True
                            return
                        yield record
                        await asyncio.sleep(0.05)
                        # CRITICAL FIX: Once the High Water Mark arrives, the device has
                        # finished streaming. Return immediately so ALDB._async_load can
                        # surgically fetch any missed middle records via num_recs=1.
                        if record.is_high_water_mark:
                            return
                        mem_addr = record.mem_addr
            except asyncio.TimeoutError:
                retries -= 1
                get_health(self._address).record_failure()

    aldb_read_manager.ALDBReadManager._read_all = _optimized_read_all
    logger.debug("Applied pyinsteon Dual-Band ALDB protocol optimizations.")


class OpenHabSafetyArbitrator:
    """Safely manages openHAB Insteon Thing to prevent port lock contention."""

    def __init__(self, base_url: str, thing_uid: str, token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.thing_uid = thing_uid
        self.token = token
        self.was_originally_enabled = True

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def check_status(self) -> bool:
        """Return True if openHAB Thing is currently reachable and enabled."""
        url = f"{self.base_url}/rest/things/{self.thing_uid}"
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                resp = await client.get(url, headers=self._headers())
                if resp.status_code == 200:
                    data = resp.json()
                    status_info = data.get("statusInfo", {})
                    status = status_info.get("status", "")
                    detail = status_info.get("statusDetail", "")
                    is_enabled = not (status == "UNINITIALIZED" and detail == "DISABLED")
                    logger.debug(f"openHAB Thing status: {status} ({detail}), enabled: {is_enabled}")
                    return is_enabled
        except Exception as ex:
            logger.warning(f"openHAB check at {url} failed: {ex}. Proceeding with caution.")
        return False

    async def fetch_insteon_labels(self) -> Dict[str, Dict[str, Any]]:
        """Query openHAB for friendly device labels and type metadata before pausing."""
        url = f"{self.base_url}/rest/things"
        metadata = {}
        battery_types = {"motion", "leak", "door", "window", "openclose", "contact", "battery", "sensor"}
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers=self._headers())
                if resp.status_code == 200:
                    for thing in resp.json():
                        config = thing.get("configuration", {})
                        props = thing.get("properties", {})
                        label = thing.get("label", "")
                        thing_type = thing.get("thingTypeUID", "")
                        addr = config.get("address") or props.get("address")
                        if not addr:
                            parts = thing.get("UID", "").split(":")
                            if parts and len(parts[-1].replace(".", "")) == 6:
                                addr = parts[-1]
                        if addr:
                            clean_addr = str(addr).upper().strip()
                            clean_compact = clean_addr.replace(".", "")
                            is_batt = any(t in thing_type.lower() or t in label.lower() for t in battery_types)
                            info = {
                                "label": label or f"Device {clean_addr}",
                                "thing_type": thing_type,
                                "is_battery": is_batt,
                            }
                            metadata[clean_addr] = info
                            metadata[clean_compact] = info
                    logger.info(f"Retrieved {len(metadata) // 2} device metadata entries from openHAB registry.")
        except Exception as ex:
            logger.debug(f"openHAB label fetch notice: {ex}")
        return metadata

    async def set_enabled(self, enable: bool) -> bool:
        """Set openHAB Thing enabled state and verify transition."""
        url = f"{self.base_url}/rest/things/{self.thing_uid}/enable"
        headers = self._headers()
        payload = "true" if enable else "false"
        state_str = "ENABLED (Online)" if enable else "DISABLED (Released for scan)"
        logger.info(f"==> openHAB Safety: Setting Insteon Thing to {state_str}...")

        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.put(url, headers={**headers, "Content-Type": "text/plain"}, content=payload)
                if resp.status_code not in (200, 202, 204):
                    resp = await client.put(url, headers=headers, json=enable)
                # Allow openHAB serial driver time to close OS file descriptor
                await asyncio.sleep(2.5)
                return True
        except Exception as ex:
            logger.error(f"    Failed to toggle openHAB Thing: {ex}")
            return False


class NetworkSnapshotCrawler:
    """Crawls Insteon PLM and line-powered device ALDBs with Dual-Band protocol optimizations."""

    def __init__(
        self,
        port: str,
        baud: int,
        workdir: str,
        per_device_timeout: float = 25.0,
        max_retries: int = 3,
        inter_device_delay: float = 0.6,
        output_file: str = "network_snapshot.json",
        force_full: bool = False,
    ):
        self.port = port
        self.baud = baud
        self.workdir = workdir
        self.per_device_timeout = per_device_timeout
        self.max_retries = max_retries
        self.inter_device_delay = inter_device_delay
        self.output_file = output_file
        self.force_full = force_full
        self.device_metadata: Dict[str, Dict[str, Any]] = {}
        self.previous_devices_cache: Dict[str, Dict[str, Any]] = {}

        self.snapshot_data: Dict[str, Any] = {
            "snapshot_metadata": {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "source": "Raspberry Pi Insteon PLM Bridge (v2.0 Dual-Band Optimized)",
                "version": "2.0",
            },
            "plm": {},
            "devices": [],
            "stats": {
                "devices_total": 0,
                "devices_success": 0,
                "devices_cached_delta_match": 0,
                "devices_partial": 0,
                "devices_failed": 0,
                "devices_skipped_battery": 0,
                "total_aldb_records": 0,
            },
        }

    def _load_previous_snapshot_cache(self):
        """Load previous snapshot file if present to support O(1) db_delta cache validation and partial resume."""
        if not os.path.isfile(self.output_file):
            return
        try:
            with open(self.output_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for dev in data.get("devices", []):
                addr = str(dev.get("address", "")).upper().strip()
                if addr:
                    self.previous_devices_cache[addr] = dev
            if self.previous_devices_cache:
                logger.info(
                    f"Loaded prior snapshot cache with {len(self.previous_devices_cache)} device entries for differential db_delta verification."
                )
        except Exception as ex:
            logger.debug(f"Could not load previous snapshot cache: {ex}")

    def _save_checkpoint(self):
        """Atomically write the current state to disk to preserve partial crawls."""
        temp_file = f"{self.output_file}.tmp"
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self.snapshot_data, f, indent=2)
            os.replace(temp_file, self.output_file)
            logger.debug(f"Saved snapshot checkpoint ({len(self.snapshot_data['devices'])} devices recorded).")
        except Exception as ex:
            logger.error(f"Error saving snapshot checkpoint: {ex}")

    def _serialize_aldb_records(self, aldb) -> List[Dict[str, Any]]:
        """Extract standardized ALDB record dictionaries from pyinsteon ALDB object."""
        records = []
        for mem_addr, rec in aldb.items():
            mode = "Controller" if rec.is_controller else ("Responder" if rec.is_responder else "Unknown")
            if not rec.is_in_use:
                mode += " (Deleted)"

            records.append({
                "mem_addr": f"0x{mem_addr:04X}" if isinstance(mem_addr, int) else str(mem_addr),
                "target_address": str(rec.target),
                "group": int(rec.group),
                "is_controller": bool(rec.is_controller),
                "is_responder": bool(rec.is_responder),
                "in_use": bool(rec.is_in_use),
                "high_water_mark": bool(getattr(rec, "is_high_water_mark", False)),
                "data1": int(rec.data1 or 0),
                "data2": int(rec.data2 or 0),
                "data3": int(rec.data3 or 0),
                "raw_mode": mode,
            })
        return records

    def _seed_aldb_from_cached_records(self, aldb, cached_records: List[Dict[str, Any]]):
        """Pre-populate a pyinsteon ALDB object with previously captured records to avoid re-reading known slots."""
        if not cached_records:
            return 0
        loaded_dict = {}
        for r in cached_records:
            try:
                mem_str = str(r.get("mem_addr", "0x0000"))
                mem_int = int(mem_str, 16) if mem_str.startswith("0x") else int(mem_str)
                rec = ALDBRecord(
                    memory=mem_int,
                    controller=bool(r.get("is_controller", False)),
                    group=int(r.get("group", 0)),
                    target=pyinsteon.Address(r.get("target_address", "00.00.00")),
                    data1=int(r.get("data1", 0)),
                    data2=int(r.get("data2", 0)),
                    data3=int(r.get("data3", 0)),
                    in_use=bool(r.get("in_use", True)),
                    high_water_mark=bool(r.get("high_water_mark", False)),
                    bit5=True,
                    bit4=False,
                )
                loaded_dict[mem_int] = rec
            except Exception:
                continue
        if loaded_dict:
            aldb.load_saved_records(ALDBStatus.PARTIAL, loaded_dict, first_mem_addr=0x0FFF)
        return len(loaded_dict)

    def _drain_outbound_queue(self):
        """Clear any stale outbound messages in the PLM protocol queue after a timeout."""
        try:
            modem = pyinsteon.devices.modem
            protocol = getattr(modem, "_protocol", None) or getattr(modem, "protocol", None)
            if protocol and hasattr(protocol, "message_queue"):
                drained = 0
                while not protocol.message_queue.empty():
                    protocol.message_queue.get_nowait()
                    drained += 1
                if drained:
                    logger.debug(f"    Drained {drained} stale messages from PLM outbound queue.")
        except Exception:
            pass

    async def _probe_device_status_and_delta(self, addr_obj) -> Tuple[bool, Optional[int], Optional[int]]:
        """
        Send a fast 10-byte Standard Direct STATUS_REQUEST (0x19 0x00) to:
        1. Verify the device is powered and reachable on the Dual-Band mesh in ~0.4s.
        2. Read its 8-bit ALDB Database Delta (`db_version` in Direct ACK cmd1).
        Returns (reachable, db_delta, on_level).
        """
        db_delta_holder: List[Optional[int]] = [None]
        on_level_holder: List[Optional[int]] = [None]

        def _on_status_ack(db_version: int, status: int):
            db_delta_holder[0] = db_version
            on_level_holder[0] = status

        cmd = StatusRequestCommand(addr_obj, status_type=0)
        cmd.subscribe(_on_status_ack, force_strong_ref=True)
        try:
            for probe_try in range(1, 3):
                try:
                    res = await asyncio.wait_for(cmd.async_send(), timeout=4.5)
                    if res == ResponseStatus.SUCCESS or db_delta_holder[0] is not None:
                        get_health(addr_obj).record_success()
                        return True, db_delta_holder[0], on_level_holder[0]
                except asyncio.TimeoutError:
                    self._drain_outbound_queue()
                if probe_try < 2:
                    await asyncio.sleep(0.6)
        finally:
            try:
                cmd.unsubscribe(_on_status_ack)
            except Exception:
                pass
        return False, None, None

    async def _fast_peek_record(self, aldb, mem_addr: int) -> Optional[ALDBRecord]:
        """
        Read a single 8-byte ALDB record using 10-byte Standard PEEK_POKE commands (0x28/0x2B),
        optimized by peeking the Control Flags byte (mem_addr - 7) FIRST:
        - If the slot is the High Water Mark (flags == 0x00 / is_hwm), returns in 1 byte (~0.4s) instead of 8!
        - If the slot is Deleted/Unused (not is_in_use), returns in 1 byte (~0.4s) instead of 8!
        - If the slot is In-Use, peeks the remaining 7 bytes (group, target, data1..3).
        """
        rm = aldb._read_manager
        get_health(aldb.address).record_success()

        flags_byte = await rm._async_peek(mem_addr=mem_addr - 7)
        if flags_byte is None or flags_byte == -1:
            return None

        flags = AllLinkRecordFlags(flags_byte)
        if flags.is_hwm:
            return ALDBRecord(
                memory=mem_addr,
                controller=False,
                group=0,
                target=pyinsteon.Address("000000"),
                data1=0,
                data2=0,
                data3=0,
                in_use=False,
                high_water_mark=True,
                bit4=False,
                bit5=False,
            )

        if not flags.is_in_use:
            return ALDBRecord(
                memory=mem_addr,
                controller=flags.is_controller,
                group=0,
                target=pyinsteon.Address("000000"),
                data1=0,
                data2=0,
                data3=0,
                in_use=False,
                high_water_mark=False,
                bit4=flags.is_bit_4_set,
                bit5=flags.is_bit_5_set,
            )

        # Active in-use record: peek the remaining 7 bytes (mem_addr - 6 up to mem_addr - 0)
        rem_bytes = {}
        for offset in range(6, -1, -1):
            val = await rm._async_peek(mem_addr=mem_addr - offset)
            if val is None or val == -1:
                return None
            rem_bytes[offset] = val

        return ALDBRecord(
            memory=mem_addr,
            controller=flags.is_controller,
            group=rem_bytes[6],
            target=pyinsteon.Address(bytearray([rem_bytes[5], rem_bytes[4], rem_bytes[3]])),
            data1=rem_bytes[2],
            data2=rem_bytes[1],
            data3=rem_bytes[0],
            in_use=True,
            high_water_mark=False,
            bit4=flags.is_bit_4_set,
            bit5=flags.is_bit_5_set,
        )

    async def _walk_missing_records_one_by_one(
        self,
        aldb,
        max_records_to_walk: int = 40,
        per_record_timeout: float = 5.5,
        use_peek_poke: bool = False,
    ) -> bool:
        """
        Surgically read missing ALDB slots one record at a time (`num_recs=1` or fast `PEEK_POKE`).
        Avoids Dual-Band simulcast self-collisions caused by continuous `num_recs=0` streams.
        """
        addr_obj = aldb.address
        get_health(addr_obj).record_success()

        # Ensure we start at 0x0FFF if no records are loaded yet
        if len(aldb) == 0:
            next_addr = 0x0FFF
        else:
            next_addr = aldb._calc_next_record()

        consecutive_erased = 0
        consecutive_timeouts = 0
        walked = 0

        while next_addr and walked < max_records_to_walk:
            if aldb.is_loaded:
                return True

            get_health(addr_obj).record_success()
            got_rec = False
            try:
                if use_peek_poke:
                    async with async_timeout.timeout(14.0):
                        rec = await self._fast_peek_record(aldb, next_addr)
                        if rec is not None:
                            aldb._add_record(rec)
                            got_rec = True
                            if rec.is_high_water_mark:
                                aldb.set_load_status()
                                return aldb.is_loaded
                else:
                    async with async_timeout.timeout(per_record_timeout):
                        async for rec in aldb._read_manager.async_read(
                            mem_addr=next_addr, num_recs=1
                        ):
                            aldb._add_record(rec)
                            got_rec = True
                            if rec.is_high_water_mark:
                                aldb.set_load_status()
                                return aldb.is_loaded
            except asyncio.TimeoutError:
                self._drain_outbound_queue()
            finally:
                await aldb._read_manager.async_stop()

            if got_rec:
                consecutive_erased = 0
                consecutive_timeouts = 0
                walked += 1
            elif aldb._read_manager.hit_erased:
                consecutive_erased += 1
                aldb._add_record(aldb._deleted_record(next_addr))
                if consecutive_erased >= 8:
                    aldb._close_erased_aldb()
                    break
            else:
                consecutive_timeouts += 1
                if consecutive_timeouts >= 2:
                    logger.debug(f"    Single-record walk paused at 0x{next_addr:04X} after 2 timeouts.")
                    break
                await asyncio.sleep(0.8)
                continue

            aldb.set_load_status()
            if aldb.is_loaded:
                return True

            # Paced breather so Dual-Band repeaters finish hopping the ACK before next query
            await asyncio.sleep(0.12 if use_peek_poke else 0.18)
            next_addr = aldb._calc_next_record()

        aldb.set_load_status()
        return aldb.is_loaded

    async def _interrogate_single_device(
        self,
        addr_str: str,
        friendly_label: str,
        dev_desc: str,
        is_recovery_pass: bool = False,
        plm_cat_hint: Tuple[int, int, int] = (1, 0, 0),
    ) -> Dict[str, Any]:
        """Interrogate a single line-powered device using smart delta check + progressive ALDB read."""
        addr_obj = pyinsteon.Address(addr_str)
        device = pyinsteon.devices.get(addr_obj)
        cat_h, subcat_h, fw_h = plm_cat_hint
        if device is None or (int(getattr(device, "subcat", 0) or 0) == 0 and subcat_h != 0):
            try:
                pyinsteon.devices.set_id(addr_obj, cat_h or 1, subcat_h, fw_h)
                await asyncio.sleep(0.05)
                device = pyinsteon.devices.get(addr_obj)
            except Exception as set_ex:
                logger.debug(f"    set_id note for {addr_str}: {set_ex}")

        aldb = getattr(device, "aldb", None)
        if aldb is None:
            aldb = ALDB(addr_obj)

        # Reset pyinsteon health backoff so previous failures never block our explicit query
        get_health(addr_obj).record_success()

        # Step 1: Fast 10-byte Standard STATUS_REQUEST probe (checks liveness + reads db_delta)
        t_probe = time.time()
        reachable, current_db_delta, on_level = await self._probe_device_status_and_delta(addr_obj)
        probe_elapsed = time.time() - t_probe

        cached_entry = self.previous_devices_cache.get(addr_str.upper())
        if reachable:
            delta_str = f"0x{current_db_delta:02X}" if current_db_delta is not None else "N/A"
            logger.info(
                f"    Ping OK in {probe_elapsed:.2f}s (DB Delta: {delta_str}, Level: {on_level})"
            )

            # Check if we can skip full ALDB read via matching db_delta!
            if (
                not self.force_full
                and cached_entry
                and cached_entry.get("status") == "OK"
                and cached_entry.get("db_delta") is not None
                and current_db_delta is not None
                and int(cached_entry.get("db_delta")) == int(current_db_delta)
                and len(cached_entry.get("records", [])) > 0
            ):
                cached_recs = cached_entry["records"]
                logger.info(
                    f"    --> FAST-SKIP: DB Delta {delta_str} unchanged! Reusing {len(cached_recs)} verified ALDB records."
                )
                return {
                    "address": addr_str,
                    "label": friendly_label,
                    "cat": int(getattr(device, "cat", 0) or cached_entry.get("cat", 0) or cat_h),
                    "subcat": int(getattr(device, "subcat", 0) or cached_entry.get("subcat", 0) or subcat_h),
                    "firmware": int(getattr(device, "firmware", 0) or cached_entry.get("firmware", 0) or fw_h),
                    "engine_version": str(getattr(device, "engine_version", "I2CS")),
                    "db_delta": current_db_delta,
                    "description": dev_desc,
                    "is_battery": False,
                    "status": "OK",
                    "records_count": len(cached_recs),
                    "records": cached_recs,
                    "_cached_delta_hit": True,
                }
        else:
            logger.warning(
                f"    No response to fast Standard Ping ({probe_elapsed:.1f}s). Device may be asleep, unplugged, or retired."
            )
            # If we already have a complete cached ALDB from an earlier awake session (e.g. Mini Keypad), preserve it!
            if cached_entry and len(cached_entry.get("records", [])) > 0:
                cached_recs = cached_entry["records"]
                logger.info(
                    f"    --> PRESERVED CACHE: Keeping {len(cached_recs)} previously captured ALDB records while device is asleep/offline."
                )
                return {
                    "address": addr_str,
                    "label": friendly_label,
                    "cat": int(getattr(device, "cat", 0) or cached_entry.get("cat", 0) or cat_h),
                    "subcat": int(getattr(device, "subcat", 0) or cached_entry.get("subcat", 0) or subcat_h),
                    "firmware": int(getattr(device, "firmware", 0) or cached_entry.get("firmware", 0) or fw_h),
                    "engine_version": cached_entry.get("engine_version", "UNKNOWN"),
                    "db_delta": cached_entry.get("db_delta"),
                    "description": dev_desc,
                    "is_battery": False,
                    "status": cached_entry.get("status", "OK"),
                    "records_count": len(cached_recs),
                    "records": cached_recs,
                    "_cached_delta_hit": True,
                }

            # In both Pass 1 and Pass 2, if a device does not even answer a 10-byte Standard Ping,
            # do not waste 60s sending 24-byte Extended ALDB reads.
            stage_msg = (
                "No response to Standard Ping (deferred to recovery pass)"
                if not is_recovery_pass
                else "Offline / Retired (No response to Standard Ping in Pass 1 or Pass 2)"
            )
            return {
                "address": addr_str,
                "label": friendly_label,
                "cat": int(getattr(device, "cat", 0) or cat_h),
                "subcat": int(getattr(device, "subcat", 0) or subcat_h),
                "firmware": int(getattr(device, "firmware", 0) or fw_h),
                "db_delta": None,
                "description": dev_desc,
                "is_battery": False,
                "status": f"FAILED: {stage_msg}",
                "records_count": 0,
                "records": [],
            }

        # Pre-seed partial records if we have a partial capture from earlier
        if (
            not self.force_full
            and len(aldb) == 0
            and cached_entry
            and str(cached_entry.get("status", "")).startswith("PARTIAL")
            and cached_entry.get("records")
        ):
            seeded = self._seed_aldb_from_cached_records(aldb, cached_entry["records"])
            if seeded:
                logger.info(f"    Seeded {seeded} previously captured records; resuming missing slots only.")

        # Step 2: Ensure Engine Version is known (helps i2CS extended messages and I1/I2 PEEK_POKE detection)
        if device is not None and getattr(device, "engine_version", EngineVersion.UNKNOWN) == EngineVersion.UNKNOWN:
            try:
                ev_res = await asyncio.wait_for(device.async_get_engine_version(), timeout=3.5)
                logger.debug(
                    f"    Engine version query for {addr_str}: response={ev_res}, version={getattr(device, 'engine_version', None)}"
                )
            except Exception:
                self._drain_outbound_queue()

        dev_engine = getattr(device, "engine_version", EngineVersion.UNKNOWN)

        # Step 3: Progressive ALDB Read (Preserve partial records across attempts: refresh=False!)
        dev_success = False
        last_error = ""
        t_start = time.time()

        for attempt in range(1, self.max_retries + 1):
            get_health(addr_obj).record_success()
            before_count = len(aldb)

            # If Attempt 1 (Extended 0x2F) returned 0 records on a reachable I1/I2 device
            # (like 18.96.F5 LampLinc 2457D2 where Extended 0x2F frames are dropped),
            # automatically switch to fast 10-byte Standard PEEK_POKE mode!
            use_peek_poke = (
                attempt > 1
                and before_count == 0
                and dev_engine in (EngineVersion.I1, EngineVersion.I2)
            )
            use_single_record_walk = is_recovery_pass or (attempt > 1) or (before_count > 0) or use_peek_poke

            if use_peek_poke:
                mode_label = f"10-byte Standard PEEK_POKE ({dev_engine.name} fallback)"
            elif use_single_record_walk:
                mode_label = "surgical 1-by-1 gap fill"
            else:
                mode_label = "bulk stream + gap fill"

            logger.info(
                f"    Attempt {attempt}/{self.max_retries} ({mode_label}, {before_count} records already in memory)..."
            )

            t0 = time.time()
            try:
                if not use_single_record_walk:
                    # Bulk stream once (with our HWM early-exit patch + automatic gap fill)
                    await asyncio.wait_for(
                        aldb.async_load(refresh=False), timeout=self.per_device_timeout
                    )
                else:
                    # Paced record-by-record walk (or fast 10-byte Standard PEEK_POKE walk)
                    await self._walk_missing_records_one_by_one(
                        aldb,
                        max_records_to_walk=35,
                        per_record_timeout=min(6.0, self.per_device_timeout / 2),
                        use_peek_poke=use_peek_poke,
                    )
            except asyncio.TimeoutError:
                self._drain_outbound_queue()
                last_error = "Timeout waiting for powerline/RF response"
                logger.warning(
                    f"    WARNING: {last_error} on attempt {attempt} ({len(aldb)} records captured so far)."
                )
            except Exception as exc:
                self._drain_outbound_queue()
                last_error = f"{type(exc).__name__}: {str(exc)}"
                logger.warning(f"    WARNING: Read error ({last_error}) on attempt {attempt}.")

            aldb.set_load_status()
            dev_records = self._serialize_aldb_records(aldb)
            elapsed = time.time() - t0

            if aldb.is_loaded or getattr(aldb.status, "name", "") == "LOADED":
                dev_success = True
                logger.info(
                    f"    SUCCESS: Complete ALDB loaded ({len(dev_records)} records) in {time.time() - t_start:.1f}s."
                )
                break

            if len(dev_records) > before_count:
                logger.info(
                    f"    Progress: Captured {len(dev_records)} records (+{len(dev_records) - before_count} new) in {elapsed:.1f}s (Status: {getattr(aldb.status, 'name', aldb.status)})."
                )

            # Check if device explicitly NAK'd the Extended 0x2F ALDB read (e.g. I2CS auth 0xFF or I1 0xFB)
            nak_code = getattr(aldb._read_manager, "_last_nak_response", None)
            if nak_code is not None and len(dev_records) == 0 and dev_engine not in (EngineVersion.I1, EngineVersion.I2):
                last_error = f"Device rejected Extended 0x2F ALDB Read with {nak_code.name} (0x{int(nak_code):02X})"
                logger.warning(f"    --> {last_error}")
                break

            if attempt < self.max_retries:
                backoff_delay = 1.5 * attempt
                logger.info(f"    Waiting {backoff_delay:.1f}s before continuing missing records...")
                await asyncio.sleep(backoff_delay)

        dev_records = self._serialize_aldb_records(aldb)
        if dev_success:
            status_str = "OK"
        elif len(dev_records) > 0:
            status_str = f"PARTIAL ({len(dev_records)} records)"
            logger.warning(
                f"    --> PARTIAL: Captured {len(dev_records)} records from {addr_str} (missing High Water Mark)."
            )
        else:
            status_str = f"FAILED: {last_error or 'Unresponsive to Extended 0x2F ALDB Read'}"
            logger.error(f"    --> UNREACHABLE: Device {addr_str} returned 0 records ({status_str}).")

        return {
            "address": addr_str,
            "label": friendly_label,
            "cat": int(getattr(device, "cat", 0) or cat_h),
            "subcat": int(getattr(device, "subcat", 0) or subcat_h),
            "firmware": int(getattr(device, "firmware", 0) or fw_h),
            "engine_version": str(getattr(device, "engine_version", "UNKNOWN")),
            "db_delta": current_db_delta,
            "description": dev_desc,
            "is_battery": False,
            "status": status_str,
            "records_count": len(dev_records),
            "records": dev_records,
        }

    async def crawl(self):
        """Execute the full network crawl with smart delta caching and two-pass recovery."""
        if not PYINSTEON_AVAILABLE:
            raise RuntimeError(
                "The 'pyinsteon' package is not installed. Run 'pip install pyinsteon pyserial pyserial-asyncio'."
            )

        os.makedirs(self.workdir, exist_ok=True)
        self._load_previous_snapshot_cache()
        start_time = time.time()

        connected = False
        for attempt in range(1, 4):
            try:
                logger.info(f"Connecting pyinsteon to PLM at {self.port} (Attempt {attempt}/3)...")
                await asyncio.wait_for(pyinsteon.async_connect(device=self.port), timeout=12.0)
                connected = True
                break
            except asyncio.TimeoutError:
                logger.warning(
                    f"    Timeout opening PLM on attempt {attempt}. Serial port may still be unbinding from openHAB..."
                )
                await asyncio.sleep(2.5)
            except Exception as ex:
                logger.warning(f"    Connect attempt {attempt} error: {ex}")
                await asyncio.sleep(2.5)

        if not connected:
            raise RuntimeError(f"Could not connect to PLM at {self.port} after 3 attempts.")

        # Apply Dual-Band protocol & ALDBReadManager optimizations immediately after connect
        apply_pyinsteon_protocol_optimizations()
        logger.info("pyinsteon connected and Dual-Band protocol optimizations active.")

        try:
            # 1. Read PLM modem info and its ALDB (local USB serial access)
            modem = pyinsteon.devices.modem
            if not modem:
                raise RuntimeError("No Insteon modem detected on serial port.")

            logger.info("\n------------------------------------------------------------")
            logger.info(f"PLM Modem Identified: {modem.address} (Cat {modem.cat}, Subcat {modem.subcat})")
            logger.info("Reading PLM All-Link Database...")

            try:
                # Allow 35s for EEPROM scan on larger PLM link tables
                await asyncio.wait_for(modem.aldb.async_load(), timeout=35.0)
            except asyncio.TimeoutError:
                logger.warning("PLM ALDB EEPROM read reached time limit; using captured modem records.")

            plm_records = self._serialize_aldb_records(modem.aldb)
            self.snapshot_data["plm"] = {
                "address": str(modem.address),
                "cat": int(modem.cat or 0),
                "subcat": int(modem.subcat or 0),
                "firmware": int(getattr(modem, "firmware", 0) or 0),
                "records_count": len(plm_records),
                "records": plm_records,
            }
            self.snapshot_data["stats"]["total_aldb_records"] += len(plm_records)
            self._save_checkpoint()

            logger.info(f"PLM All-Link Database read: {len(plm_records)} records captured.")

            # 2. Extract discovered device addresses and PLM hardware hints (from Group 0 Controller records)
            discovered_addresses = set()
            plm_cat_hints: Dict[str, Tuple[int, int, int]] = {}
            for rec in plm_records:
                if rec["in_use"] and rec["target_address"] != str(modem.address):
                    t_addr = rec["target_address"]
                    discovered_addresses.add(t_addr)
                    if rec.get("group") == 0 and rec.get("data1", 0) > 0:
                        plm_cat_hints[t_addr] = (
                            int(rec.get("data1", 0)),
                            int(rec.get("data2", 0)),
                            int(rec.get("data3", 0)),
                        )

            for addr in pyinsteon.devices:
                if str(addr) != str(modem.address):
                    discovered_addresses.add(str(addr))

            sorted_addresses = sorted(list(discovered_addresses))
            self.snapshot_data["stats"]["devices_total"] = len(sorted_addresses)

            logger.info("\n------------------------------------------------------------")
            logger.info(f"Discovered {len(sorted_addresses)} unique Insteon peer devices to query:")
            for addr in sorted_addresses:
                clean_lookup = addr.replace(".", "").upper()
                meta = self.device_metadata.get(addr, {}) or self.device_metadata.get(clean_lookup, {})
                lbl = meta.get("label", "")
                lbl_str = f" ({lbl})" if lbl else ""
                batt_str = " [Battery Sensor]" if meta.get("is_battery") else ""
                logger.info(f"  * {addr}{lbl_str}{batt_str}")
            logger.info("------------------------------------------------------------\n")

            # 3. PASS 1: Primary Interrogation Pass
            straggler_indices: List[int] = []

            for index, addr_str in enumerate(sorted_addresses, start=1):
                clean_lookup = addr_str.replace(".", "").upper()
                meta = self.device_metadata.get(addr_str, {}) or self.device_metadata.get(clean_lookup, {})
                friendly_label = meta.get("label") or "Line Device"
                is_battery_hint = meta.get("is_battery", False)

                logger.info(f"[{index}/{len(sorted_addresses)}] Querying device {addr_str} ({friendly_label})...")

                addr_obj = pyinsteon.Address(addr_str)
                device = pyinsteon.devices.get(addr_obj)
                is_battery = bool(getattr(device, "is_battery", False)) or is_battery_hint

                if is_battery:
                    logger.info(f"    --> SKIPPED: Battery-powered device ({friendly_label}) (sleeping).")
                    self.snapshot_data["devices"].append({
                        "address": addr_str,
                        "label": friendly_label,
                        "description": getattr(device, "description", meta.get("thing_type", "Battery Device")),
                        "is_battery": True,
                        "status": "SKIPPED_BATTERY",
                        "records_count": 0,
                        "records": [],
                    })
                    self.snapshot_data["stats"]["devices_skipped_battery"] += 1
                    self._save_checkpoint()
                    continue

                dev_desc = getattr(device, "description", "") or meta.get("thing_type", "") or friendly_label
                dev_entry = await self._interrogate_single_device(
                    addr_str=addr_str,
                    friendly_label=friendly_label,
                    dev_desc=dev_desc,
                    is_recovery_pass=False,
                    plm_cat_hint=plm_cat_hints.get(addr_str, (0, 0, 0)),
                )

                dev_idx = len(self.snapshot_data["devices"])
                self.snapshot_data["devices"].append(dev_entry)
                if dev_entry["status"] != "OK":
                    straggler_indices.append(dev_idx)

                self._save_checkpoint()
                await asyncio.sleep(self.inter_device_delay)

            # 4. PASS 2: Automatic Straggler Recovery Pass (for PARTIAL or FAILED devices)
            if straggler_indices:
                logger.info("\n------------------------------------------------------------")
                logger.info(
                    f"PASS 2: Running Targeted Recovery Pass for {len(straggler_indices)} incomplete device(s)..."
                )
                logger.info("------------------------------------------------------------")
                await asyncio.sleep(2.0)

                for dev_idx in straggler_indices:
                    prev_entry = self.snapshot_data["devices"][dev_idx]
                    addr_str = prev_entry["address"]
                    friendly_label = prev_entry["label"]
                    dev_desc = prev_entry["description"]

                    logger.info(
                        f"[Recovery] Re-querying {addr_str} ({friendly_label}) (Currently: {prev_entry['status']})..."
                    )
                    recovered_entry = await self._interrogate_single_device(
                        addr_str=addr_str,
                        friendly_label=friendly_label,
                        dev_desc=dev_desc,
                        is_recovery_pass=True,
                        plm_cat_hint=plm_cat_hints.get(addr_str, (0, 0, 0)),
                    )
                    if recovered_entry["records_count"] >= prev_entry["records_count"]:
                        self.snapshot_data["devices"][dev_idx] = recovered_entry
                    self._save_checkpoint()
                    await asyncio.sleep(self.inter_device_delay)

            # 5. Compute final statistics
            for dev_entry in self.snapshot_data["devices"]:
                if dev_entry.get("is_battery"):
                    continue
                st = dev_entry.get("status", "")
                self.snapshot_data["stats"]["total_aldb_records"] += dev_entry.get("records_count", 0)
                if st == "OK" or st.startswith("CACHED_SLEEPING"):
                    self.snapshot_data["stats"]["devices_success"] += 1
                    if dev_entry.get("_cached_delta_hit"):
                        self.snapshot_data["stats"]["devices_cached_delta_match"] += 1
                    dev_entry.pop("_cached_delta_hit", None)
                elif st.startswith("PARTIAL"):
                    self.snapshot_data["stats"]["devices_partial"] += 1
                else:
                    self.snapshot_data["stats"]["devices_failed"] += 1

            total_elapsed = time.time() - start_time
            self.snapshot_data["stats"]["total_duration_seconds"] = round(total_elapsed, 1)
            self._save_checkpoint()

            logger.info("\n============================================================")
            logger.info("            NETWORK SCAN & ALDB AUDIT COMPLETE              ")
            logger.info("============================================================")
            logger.info(f"Total Devices Scanned:  {self.snapshot_data['stats']['devices_total']}")
            logger.info(f"Successful Devices:     {self.snapshot_data['stats']['devices_success']} (Delta Cache Hits: {self.snapshot_data['stats']['devices_cached_delta_match']})")
            logger.info(f"Partial ALDB Captures:  {self.snapshot_data['stats']['devices_partial']}")
            logger.info(f"Failed / Unreachable:   {self.snapshot_data['stats']['devices_failed']}")
            logger.info(f"Skipped (Battery):      {self.snapshot_data['stats']['devices_skipped_battery']}")
            logger.info(f"Total ALDB Records:     {self.snapshot_data['stats']['total_aldb_records']}")
            logger.info(f"Total Elapsed Time:     {total_elapsed:.1f} seconds")
            logger.info(f"Snapshot written to:    {os.path.abspath(self.output_file)}")
            logger.info(f"Diagnostic log:         {os.path.abspath(LOG_FILENAME)}")
            logger.info("============================================================\n")

        finally:
            logger.info("Disconnecting pyinsteon and freeing serial port...")
            try:
                await pyinsteon.async_close()
            except Exception as ex:
                logger.debug(f"pyinsteon close notice: {ex}")


async def main():
    parser = argparse.ArgumentParser(
        description="Safely extract complete Insteon network topology and device ALDBs from Raspberry Pi."
    )
    parser.add_argument("--port", default=DEFAULT_PORT, help=f"Serial port for Insteon PLM (default: {DEFAULT_PORT})")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help=f"PLM baud rate (default: {DEFAULT_BAUD})")
    parser.add_argument("--openhab-url", default=DEFAULT_OH_URL, help=f"openHAB REST API URL (default: {DEFAULT_OH_URL})")
    parser.add_argument("--openhab-thing", default=DEFAULT_OH_THING, help=f"openHAB Insteon Thing UID (default: {DEFAULT_OH_THING})")
    parser.add_argument("--openhab-token", default=DEFAULT_OH_TOKEN, help="Optional openHAB API token (defaults to .env)")
    parser.add_argument("--skip-openhab", action="store_true", help="Skip toggling openHAB (if already stopped)")
    parser.add_argument("--timeout", type=float, default=22.0, help="Per-device bulk ALDB read timeout in seconds")
    parser.add_argument("--retries", type=int, default=3, help="Max progressive attempts per device")
    parser.add_argument("--delay", type=float, default=0.6, help="Inter-device settle delay in seconds")
    parser.add_argument("--output", default="network_snapshot.json", help="Output JSON snapshot file path")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR, help=f"pyinsteon cache directory (default: {DEFAULT_CACHE_DIR})")
    parser.add_argument("--force-full", action="store_true", help="Force full ALDB read even if device DB Delta matches cached snapshot")
    args = parser.parse_args()

    arbitrator = OpenHabSafetyArbitrator(
        base_url=args.openhab_url,
        thing_uid=args.openhab_thing,
        token=args.openhab_token,
    )

    crawler = NetworkSnapshotCrawler(
        port=args.port,
        baud=args.baud,
        workdir=args.cache_dir,
        per_device_timeout=args.timeout,
        max_retries=args.retries,
        inter_device_delay=args.delay,
        output_file=args.output,
        force_full=args.force_full,
    )

    loop = asyncio.get_running_loop()

    def handle_signal():
        logger.warning("\n[INTERRUPT] Received cancellation signal (Ctrl+C). Initiating emergency safety shutdown...")

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            pass

    try:
        if not args.skip_openhab:
            metadata = await arbitrator.fetch_insteon_labels()
            crawler.device_metadata = metadata

            is_online = await arbitrator.check_status()
            if is_online:
                logger.info("openHAB Insteon Thing is currently ACTIVE.")
                logger.info("Pausing openHAB Insteon Thing to release serial port...")
                await arbitrator.set_enabled(False)
            else:
                logger.info("openHAB Insteon Thing is already PAUSED or unreachable. Proceeding.")

        await crawler.crawl()
    except Exception as exc:
        logger.error(f"Fatal error during network crawl: {exc}", exc_info=True)
    finally:
        if not args.skip_openhab:
            logger.info("\n==> Restoring openHAB Insteon Thing back to ONLINE...")
            await arbitrator.set_enabled(True)
            logger.info("openHAB restored. Hardware arbitration complete.\n")


if __name__ == "__main__":
    asyncio.run(main())
