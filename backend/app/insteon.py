"""Insteon hardware interaction layer using pyinsteon."""

import os
import json
import logging
import asyncio
from typing import List, Optional, Dict, Any

# Tune pyinsteon serial write pacing before importing pyinsteon
os.environ.setdefault("PYINSTEON_WRITE_WAIT", "0.30")

from app.config import settings
from app.models import ALDBRecordResponse, PlmInfoResponse, DeviceSummary

logger = logging.getLogger(__name__)

# Try importing pyinsteon; if not installed (e.g. during mock setup), handle gracefully
try:
    import pyinsteon
    from pyinsteon.address import Address
    from pyinsteon.aldb.aldb import ALDB
    from pyinsteon.aldb.aldb_record import ALDBRecord
    from pyinsteon.constants import ALDBStatus, EngineVersion, ReadWriteMode, ResponseStatus
    from pyinsteon.data_types.all_link_record_flags import AllLinkRecordFlags
    from pyinsteon.handlers.to_device.engine_version_request import EngineVersionRequest
    from pyinsteon.handlers.to_device.id_request import IdRequestCommand
    from pyinsteon.handlers.to_device.peek import PeekCommand
    from pyinsteon.handlers.to_device.set_msb import SetMsbCommand as SetMSBAddressCommand
    from pyinsteon.handlers.to_device.status_request import StatusRequestCommand
    from pyinsteon.managers import aldb_read_manager
    from pyinsteon.managers.link_manager.default_links import async_add_default_links
    from pyinsteon.topics import ADD_DEFAULT_LINKS
    from pyinsteon.utils import unsubscribe_topic
    import async_timeout
    try:
        from pyinsteon.managers.device_health import get_health
    except ImportError:
        class _DummyHealth:
            def can_continue_operation(self):
                return True
            def record_failure(self):
                pass
            def record_success(self):
                pass
        def get_health(_addr):
            return _DummyHealth()
    PYINSTEON_AVAILABLE = True

except ImportError as _imp_err:
    PYINSTEON_AVAILABLE = False
    logger.warning(f"pyinsteon library is not installed ({_imp_err}). Running in mock/compatibility mode.")

    class ALDBStatus:
        EMPTY = 0
        LOADING = 1
        LOADED = 2
        PARTIAL = 3
        FAILED = 4

    class EngineVersion:
        UNKNOWN = -1
        I1 = 0
        I2 = 1
        I2CS = 2

    class ReadWriteMode:
        STANDARD = 0
        PEEK_POKE = 1
        EEPROM = 2

    class ResponseStatus:
        SUCCESS = 1
        FAILURE = 0
        UNSENT = -1

    class Address:
        def __init__(self, addr):
            self._addr = str(addr)
            self.id = self._addr.replace(".", "")
        def __str__(self):
            return self._addr



def _apply_pyinsteon_optimizations():
    """Patch pyinsteon for collision-free Dual-Band ALDB operations."""
    if not PYINSTEON_AVAILABLE:
        return
    pyinsteon.devices.delay_inspection = True
    try:
        unsubscribe_topic(async_add_default_links, ADD_DEFAULT_LINKS)
    except Exception:
        pass
    aldb_read_manager.RETRIES_ALL_MAX = 1
    aldb_read_manager.RETRIES_ONE_MAX = 3
    aldb_read_manager.TIMER_RECORD = 3.5

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
                getattr(ResponseStatus, "DIRECT_NAK_PRE_NAK", None),
            ]:
                self._last_nak_response = response
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
                        if record.is_high_water_mark:
                            return
                        mem_addr = record.mem_addr
            except asyncio.TimeoutError:
                retries -= 1
                get_health(self._address).record_failure()

    aldb_read_manager.ALDBReadManager._read_all = _optimized_read_all


class InsteonManager:
    """Manages connection lifecycle and ALDB operations for pyinsteon."""

    def __init__(self):
        self.is_connected = False
        self._lock = asyncio.Lock()
        self._mock_mode = False

    async def connect(self, retries: int = 3, retry_delay: float = 2.0) -> bool:
        """Connect to the Insteon PLM over the configured serial port."""
        async with self._lock:
            if self.is_connected:
                logger.info("pyinsteon is already connected.")
                return True

            os.makedirs(settings.cache_dir, exist_ok=True)

            if not PYINSTEON_AVAILABLE or settings.mock_mode or (os.name == "nt" and settings.insteon_port.startswith("/dev/")):
                logger.info("Simulator / mock mode enabled for Insteon session.")
                self._mock_mode = True
                self.is_connected = True
                return True

            for attempt in range(1, retries + 1):
                try:
                    logger.info(
                        f"Connecting pyinsteon to {settings.insteon_port} (Attempt {attempt}/{retries})..."
                    )
                    await pyinsteon.async_connect(device=settings.insteon_port)
                    _apply_pyinsteon_optimizations()
                    if hasattr(pyinsteon.devices, "async_load"):
                        try:
                            await pyinsteon.devices.async_load(
                                workdir=settings.cache_dir, id_devices=0, load_modem_aldb=0
                            )
                        except Exception:
                            pass
                    self.is_connected = True
                    logger.info("Successfully connected pyinsteon to PLM.")
                    return True
                except Exception as exc:
                    logger.error(
                        f"Failed connecting pyinsteon on attempt {attempt}: {exc}"
                    )
                    if attempt < retries:
                        await asyncio.sleep(retry_delay * attempt)
                    else:
                        logger.error(
                            f"All {retries} connection attempts to {settings.insteon_port} failed."
                        )
                        raise

            return False

    async def disconnect(self):
        """Disconnect pyinsteon cleanly and release serial port."""
        async with self._lock:
            if not self.is_connected:
                return

            if self._mock_mode:
                self.is_connected = False
                logger.info("pyinsteon (mock) disconnected.")
                return

            if PYINSTEON_AVAILABLE:
                try:
                    logger.info("Disconnecting pyinsteon and releasing serial port...")
                    await pyinsteon.async_close()
                except Exception as exc:
                    logger.error(f"Error closing pyinsteon connection: {exc}")
                finally:
                    self.is_connected = False
            else:
                self.is_connected = False

    async def get_plm_info(self, force_refresh_aldb: bool = False) -> PlmInfoResponse:
        """Get PLM device info and its All-Link Database."""
        if not self.is_connected:
            snapshot = self._load_snapshot()
            if snapshot and "plm" in snapshot and snapshot["plm"]:
                return self._get_mock_plm_info()
            if self._mock_mode or not PYINSTEON_AVAILABLE:
                return self._get_mock_plm_info()
            raise RuntimeError("pyinsteon is not connected. Start a session first.")

        if self._mock_mode or not PYINSTEON_AVAILABLE:
            return self._get_mock_plm_info()

        modem = pyinsteon.devices.modem
        if not modem:
            raise RuntimeError("No PLM modem device detected by pyinsteon.")

        if force_refresh_aldb or not modem.aldb.is_loaded:
            logger.info("Loading PLM ALDB from hardware...")
            try:
                # Allow up to 60s for full PLM EEPROM read
                await asyncio.wait_for(modem.aldb.async_load(), timeout=60.0)
            except asyncio.TimeoutError:
                logger.warning("Timeout loading PLM ALDB from hardware; returning captured records.")

        records = self._parse_aldb_records(modem.aldb, source_address=str(modem.address))

        return PlmInfoResponse(
            address=str(modem.address),
            cat=int(modem.cat or 0),
            subcat=int(modem.subcat or 0),
            firmware=int(getattr(modem, "firmware", 0) or 0),
            records_count=len(records),
            records=records,
        )

    def _parse_aldb_records(self, aldb, source_address: str = "") -> List[ALDBRecordResponse]:
        """Convert pyinsteon ALDB records into standardized API models."""
        results: List[ALDBRecordResponse] = []
        for mem_addr, record in aldb.items():
            mode_str = "Controller" if record.is_controller else ("Responder" if record.is_responder else "Unknown")
            if not record.is_in_use:
                mode_str += " (Deleted)"

            results.append(
                ALDBRecordResponse(
                    mem_addr=f"0x{mem_addr:04X}" if isinstance(mem_addr, int) else str(mem_addr),
                    source_address=source_address,
                    target_address=str(record.target),
                    group=record.group,
                    is_controller=bool(record.is_controller),
                    is_responder=bool(record.is_responder),
                    in_use=bool(record.is_in_use),
                    high_water_mark=bool(getattr(record, "is_high_water_mark", False)),
                    data1=int(record.data1 or 0),
                    data2=int(record.data2 or 0),
                    data3=int(record.data3 or 0),
                    raw_mode=mode_str,
                )
            )
        return results

    def _drain_outbound_queue(self):
        """Clear any stale outbound messages in the PLM protocol queue after a timeout."""
        try:
            modem = pyinsteon.devices.modem
            protocol = getattr(modem, "_protocol", None) or getattr(modem, "protocol", None)
            if protocol and hasattr(protocol, "message_queue"):
                while not protocol.message_queue.empty():
                    protocol.message_queue.get_nowait()
        except Exception:
            pass

    async def _fast_ping_and_db_delta(self, addr_obj, timeout: float = 4.5):
        """Send a 10-byte Standard StatusRequest (0x19) to verify reachability and capture db_delta."""
        db_delta_holder: List[Optional[int]] = [None]
        on_level_holder: List[Optional[int]] = [None]

        def _on_status_ack(db_version: int, status: int):
            db_delta_holder[0] = int(db_version)
            on_level_holder[0] = int(status)

        cmd = StatusRequestCommand(addr_obj, status_type=0)
        cmd.subscribe(_on_status_ack, force_strong_ref=True)
        try:
            res = await asyncio.wait_for(cmd.async_send(), timeout=timeout)
            if res == ResponseStatus.SUCCESS or db_delta_holder[0] is not None:
                get_health(addr_obj).record_success()
                return True, db_delta_holder[0], on_level_holder[0]
        except asyncio.TimeoutError:
            self._drain_outbound_queue()
        except Exception:
            pass
        finally:
            try:
                cmd.unsubscribe(_on_status_ack)
            except Exception:
                pass
        return False, None, None

    async def _fast_peek_record(self, aldb, mem_addr: int):
        """Read a single 8-byte ALDB record using 10-byte Standard PEEK_POKE (I1/I2 fallback)."""
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
                target=Address("000000"),
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
                target=Address("000000"),
                data1=0,
                data2=0,
                data3=0,
                in_use=False,
                high_water_mark=False,
                bit4=flags.is_bit_4_set,
                bit5=flags.is_bit_5_set,
            )

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
            target=Address(bytearray([rem_bytes[5], rem_bytes[4], rem_bytes[3]])),
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
        """Surgically read missing ALDB slots one record at a time (num_recs=1 or fast PEEK_POKE)."""
        addr_obj = aldb.address
        get_health(addr_obj).record_success()

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
                    break
                await asyncio.sleep(0.8)
                continue

            aldb.set_load_status()
            if aldb.is_loaded:
                return True

            await asyncio.sleep(0.12 if use_peek_poke else 0.18)
            next_addr = aldb._calc_next_record()

        aldb.set_load_status()
        return aldb.is_loaded

    async def _read_aldb_robust(self, aldb, prefer_bulk: bool = True, use_peek_poke: bool = False) -> ALDBStatus:
        """Read missing ALDB records using bulk stream + 1-by-1 gap filling or 10-byte PEEK_POKE."""
        get_health(aldb.address).record_success()
        if prefer_bulk and not use_peek_poke and len(aldb) == 0:
            try:
                await asyncio.wait_for(aldb.async_load(refresh=False), timeout=22.0)
            except asyncio.TimeoutError:
                self._drain_outbound_queue()
            except Exception:
                self._drain_outbound_queue()
            aldb.set_load_status()
            return aldb.status

        await self._walk_missing_records_one_by_one(
            aldb,
            max_records_to_walk=35,
            per_record_timeout=6.0,
            use_peek_poke=use_peek_poke,
        )
        aldb.set_load_status()
        return aldb.status


    async def get_device_aldb(self, address: str, force_refresh: bool = False) -> List[ALDBRecordResponse]:
        """Get or refresh a single device's ALDB using differential db_delta check and I2 PEEK_POKE fallback."""
        clean_addr = str(Address(address)) if PYINSTEON_AVAILABLE else address.upper()
        snapshot = self._load_snapshot() or {}
        cached_dev = None
        for d in snapshot.get("devices", []):
            if d.get("address", "").upper() == clean_addr.upper():
                cached_dev = d
                break

        if not self.is_connected or self._mock_mode or not PYINSTEON_AVAILABLE or not force_refresh:
            if cached_dev and cached_dev.get("records"):
                return [
                    ALDBRecordResponse(
                        mem_addr=r.get("mem_addr", "0x0000"),
                        source_address=clean_addr,
                        target_address=r.get("target_address", ""),
                        group=r.get("group", 1),
                        is_controller=r.get("is_controller", False),
                        is_responder=r.get("is_responder", False),
                        in_use=r.get("in_use", True),
                        high_water_mark=r.get("high_water_mark", False),
                        data1=r.get("data1", 0),
                        data2=r.get("data2", 0),
                        data3=r.get("data3", 0),
                        raw_mode=r.get("raw_mode", ""),
                    )
                    for r in cached_dev.get("records", [])
                ]
            if not self.is_connected:
                if self._mock_mode or not PYINSTEON_AVAILABLE:
                    return []
                raise RuntimeError("pyinsteon is not connected. Start a session first.")

        if (self._mock_mode or not PYINSTEON_AVAILABLE) or (not force_refresh and cached_dev and cached_dev.get("records")):
            if cached_dev:
                return [
                    ALDBRecordResponse(
                        mem_addr=r.get("mem_addr", "0x0000"),
                        source_address=clean_addr,
                        target_address=r.get("target_address", ""),
                        group=r.get("group", 1),
                        is_controller=r.get("is_controller", False),
                        is_responder=r.get("is_responder", False),
                        in_use=r.get("in_use", True),
                        high_water_mark=r.get("high_water_mark", False),
                        data1=r.get("data1", 0),
                        data2=r.get("data2", 0),
                        data3=r.get("data3", 0),
                        raw_mode=r.get("raw_mode", ""),
                    )
                    for r in cached_dev.get("records", [])
                ]
            if self._mock_mode or not PYINSTEON_AVAILABLE:
                return []

        addr_obj = Address(clean_addr)
        dev_label = (cached_dev or {}).get("label") or (cached_dev or {}).get("description") or clean_addr
        logger.info(f"[Device Query] Pinging {clean_addr} ({dev_label})...")
        ping_ok, live_delta, _ = await self._fast_ping_and_db_delta(addr_obj, timeout=6.0)
        if not ping_ok:
            if cached_dev and cached_dev.get("records"):
                logger.info(f"Device {clean_addr} ({dev_label}) asleep/offline; returning {len(cached_dev['records'])} cached ALDB records.")
                return [
                    ALDBRecordResponse(
                        mem_addr=r.get("mem_addr", "0x0000"),
                        source_address=clean_addr,
                        target_address=r.get("target_address", ""),
                        group=r.get("group", 1),
                        is_controller=r.get("is_controller", False),
                        is_responder=r.get("is_responder", False),
                        in_use=r.get("in_use", True),
                        high_water_mark=r.get("high_water_mark", False),
                        data1=r.get("data1", 0),
                        data2=r.get("data2", 0),
                        data3=r.get("data3", 0),
                        raw_mode=r.get("raw_mode", ""),
                    )
                    for r in cached_dev.get("records", [])
                ]
            logger.warning(f"Device {clean_addr} ({dev_label}) did not respond to ping.")
            return []

        if (
            not force_refresh
            and cached_dev
            and cached_dev.get("status") == "OK"
            and cached_dev.get("db_delta") is not None
            and int(cached_dev.get("db_delta")) == live_delta
            and cached_dev.get("records")
        ):
            logger.info(f"Fast-skipping {clean_addr} ({dev_label}): db_delta 0x{live_delta:02X} unchanged.")
            return [
                ALDBRecordResponse(
                    mem_addr=r.get("mem_addr", "0x0000"),
                    source_address=clean_addr,
                    target_address=r.get("target_address", ""),
                    group=r.get("group", 1),
                    is_controller=r.get("is_controller", False),
                    is_responder=r.get("is_responder", False),
                    in_use=r.get("in_use", True),
                    high_water_mark=r.get("high_water_mark", False),
                    data1=r.get("data1", 0),
                    data2=r.get("data2", 0),
                    data3=r.get("data3", 0),
                    raw_mode=r.get("raw_mode", ""),
                )
                for r in cached_dev.get("records", [])
            ]

        device = pyinsteon.devices.get(addr_obj)
        if not device:
            cat_c = int((cached_dev or {}).get("cat", 1) or 1)
            subcat_c = int((cached_dev or {}).get("subcat", 0) or 0)
            fw_c = int((cached_dev or {}).get("firmware", 0) or 0)
            device = pyinsteon.devices.add_x10_or_insteon_device(addr_obj, cat_c, subcat_c, fw_c)
        if force_refresh and hasattr(device, "aldb"):
            try:
                device.aldb.clear()
            except Exception:
                pass
        if getattr(device, "engine_version", EngineVersion.UNKNOWN) == EngineVersion.UNKNOWN:
            try:
                await asyncio.wait_for(device.async_get_engine_version(), timeout=3.5)
            except Exception:
                self._drain_outbound_queue()

        use_peek_poke = getattr(device, "engine_version", EngineVersion.UNKNOWN) == EngineVersion.I1
        for attempt in range(1, 4):
            prefer_bulk = attempt == 1 and len(device.aldb) == 0
            mode_desc = "10-byte Standard PEEK_POKE" if use_peek_poke else ("bulk stream" if prefer_bulk else "1-by-1 gap fill")
            logger.info(f"[Device Query] Reading ALDB from {clean_addr} ({dev_label}) Attempt {attempt}/3 ({mode_desc})...")
            try:
                timeout_s = 65.0 if use_peek_poke else 22.0
                await asyncio.wait_for(
                    self._read_aldb_robust(device.aldb, prefer_bulk=prefer_bulk, use_peek_poke=use_peek_poke),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                self._drain_outbound_queue()
            if device.aldb.is_loaded:
                break
            if len(device.aldb) == 0 and not use_peek_poke and device.engine_version in (EngineVersion.I1, EngineVersion.I2):
                use_peek_poke = True
                logger.info(f"[Device Query] {clean_addr} ({dev_label}): Switching to 10-byte Standard PEEK_POKE...")
            await asyncio.sleep(1.5 * attempt)

        parsed = self._parse_aldb_records(device.aldb, source_address=clean_addr)
        if parsed:
            logger.info(f"[Device Query] {clean_addr} ({dev_label}) ALDB read complete ({len(parsed)} records).")
            self._update_device_in_snapshot(
                address=clean_addr,
                device=device,
                live_delta=live_delta,
                records=parsed,
                is_loaded=bool(device.aldb.is_loaded),
            )
        return parsed


    @staticmethod
    def is_active_snapshot_device(d: Dict[str, Any]) -> bool:
        """Return True if a snapshot device entry represents a real, active device (not a retired/failed 0-record ghost)."""
        status_str = str(d.get("status", "OK")).upper()
        if status_str == "REPLACED":
            return False
        if status_str.startswith("FAILED") and len(d.get("records", [])) == 0 and not d.get("is_battery", False):
            return False
        return True

    def _seed_aldb_from_snapshot_records(self, aldb, records: List[Dict[str, Any]], is_modem: bool = False) -> int:
        """Seed a pyinsteon ALDB or ModemALDB instance from verified snapshot records, ensuring a High-Water Mark exists."""
        if not PYINSTEON_AVAILABLE or not records:
            return 0

        loaded_dict: Dict[int, Any] = {}
        min_mem = 0x1FFF if is_modem else 0x0FFF
        has_hwm = False

        for r in records:
            try:
                mem_str = str(r.get("mem_addr", "0x0FFF"))
                mem_int = int(mem_str, 16) if mem_str.upper().startswith("0X") else int(mem_str)
                if mem_int < min_mem:
                    min_mem = mem_int

                is_ctrl = bool(r.get("is_controller", False))
                in_use = bool(r.get("in_use", True))
                is_hwm = bool(r.get("high_water_mark", False))
                if is_hwm:
                    has_hwm = True

                rec = ALDBRecord(
                    memory=mem_int,
                    controller=is_ctrl,
                    group=int(r.get("group", 0)),
                    target=Address(r.get("target_address", "00.00.00")),
                    data1=int(r.get("data1", 0)),
                    data2=int(r.get("data2", 0)),
                    data3=int(r.get("data3", 0)),
                    in_use=in_use,
                    high_water_mark=is_hwm,
                    bit5=True,
                    bit4=False,
                )
                loaded_dict[mem_int] = rec
            except Exception as ex:
                logger.debug(f"Skipping invalid snapshot record during ALDB seed: {ex}")

        # Ensure non-modem ALDB has a High-Water Mark record at the end so pyinsteon knows where the table terminates
        if not is_modem and loaded_dict and not has_hwm:
            hwm_addr = max(0x0000, min_mem - 8)
            loaded_dict[hwm_addr] = ALDBRecord(
                memory=hwm_addr,
                controller=False,
                group=0,
                target=Address("00.00.00"),
                data1=0,
                data2=0,
                data3=0,
                in_use=False,
                high_water_mark=True,
                bit5=False,
                bit4=False,
            )

        if loaded_dict:
            try:
                if hasattr(aldb, "load_saved_records"):
                    aldb.load_saved_records(
                        ALDBStatus.LOADED,
                        loaded_dict,
                        first_mem_addr=0x1FFF if is_modem else 0x0FFF,
                    )
                else:
                    aldb._records.update(loaded_dict)
                if hasattr(aldb, "set_load_status"):
                    aldb.set_load_status()
            except Exception as ex:
                logger.debug(f"ALDB load_saved_records notice: {ex}")
                aldb._records.update(loaded_dict)
        return len(loaded_dict)

    async def ensure_device_aldb_ready_for_write(
        self, address: str, snapshot: Optional[Dict[str, Any]] = None
    ) -> tuple[bool, Optional[Any], str]:
        """Verify reachability and ensure pyinsteon's in-memory ALDB is fully populated before a hardware write."""
        if not self.is_connected or self._mock_mode or not PYINSTEON_AVAILABLE:
            return False, None, "Not connected to live hardware"

        clean_addr = str(Address(address)).upper()
        snapshot = snapshot or self._load_snapshot() or {}
        modem = pyinsteon.devices.modem
        modem_addr = str(modem.address).upper() if modem else ""

        # Case 1: USB PowerLinc Modem (PLM)
        if clean_addr == modem_addr:
            if not modem:
                return False, None, "PLM modem object not found."
            if len(modem.aldb) == 0:
                try:
                    await asyncio.wait_for(modem.aldb.async_load(), timeout=30.0)
                except Exception as ex:
                    logger.warning(f"Live PLM ALDB load timed out ({ex}); seeding from verified snapshot...")
                if len(modem.aldb) == 0:
                    plm_recs = snapshot.get("plm", {}).get("records", [])
                    self._seed_aldb_from_snapshot_records(modem.aldb, plm_recs, is_modem=True)
            return True, modem, "PLM ALDB ready"

        # Case 2: Peer Insteon Device
        cached_dev = None
        for d in snapshot.get("devices", []):
            if d.get("address", "").upper() == clean_addr:
                cached_dev = d
                break

        addr_obj = Address(clean_addr)
        get_health(addr_obj).record_success()

        ping_ok, live_delta, _ = await self._fast_ping_and_db_delta(addr_obj, timeout=5.5)
        if not ping_ok:
            is_batt = bool((cached_dev or {}).get("is_battery", False)) or int((cached_dev or {}).get("cat", 1) or 1) in (0x00, 0x10)
            desc = (cached_dev or {}).get("label") or (cached_dev or {}).get("description") or clean_addr
            if is_batt or "keypad" in desc.lower() or "remote" in desc.lower():
                return (
                    False,
                    None,
                    f"Device {clean_addr} ({desc}) did not respond to ping. If it is a battery-powered Mini Keypad/Remote, hold its Set button to wake it before writing to hardware.",
                )
            return (
                False,
                None,
                f"Device {clean_addr} ({desc}) is unreachable on the powerline/RF mesh (failed 0x19 status ping). Write aborted.",
            )

        cat = int((cached_dev or {}).get("cat", 1) or 1)
        subcat = int((cached_dev or {}).get("subcat", 0) or 0)
        fw = int((cached_dev or {}).get("firmware", 0) or 0)

        dev_obj = pyinsteon.devices.get(addr_obj)
        if not dev_obj:
            try:
                pyinsteon.devices.set_id(addr_obj, cat, subcat, fw)
                dev_obj = pyinsteon.devices.get(addr_obj)
            except Exception:
                pass
        if not dev_obj:
            dev_obj = pyinsteon.devices.add_x10_or_insteon_device(addr_obj, cat, subcat, fw)

        if getattr(dev_obj, "engine_version", EngineVersion.UNKNOWN) == EngineVersion.UNKNOWN:
            eng_str = str((cached_dev or {}).get("engine_version", "I2CS")).upper()
            if "I1" in eng_str:
                dev_obj.engine_version = EngineVersion.I1
            elif "I2" in eng_str and "I2CS" not in eng_str:
                dev_obj.engine_version = EngineVersion.I2
            else:
                dev_obj.engine_version = EngineVersion.I2CS

        # Check if we can seed from verified snapshot via matching db_delta
        snap_delta = (cached_dev or {}).get("db_delta")
        snap_records = (cached_dev or {}).get("records", [])
        if (
            len(dev_obj.aldb) == 0
            and snap_delta is not None
            and live_delta is not None
            and int(snap_delta) == int(live_delta)
            and len(snap_records) > 0
        ):
            seeded_count = self._seed_aldb_from_snapshot_records(dev_obj.aldb, snap_records, is_modem=False)
            logger.info(
                f"Seeded {seeded_count} verified ALDB records into pyinsteon memory for {clean_addr} (db_delta=0x{live_delta:02X})."
            )

        # If still not loaded (e.g. db_delta changed on hardware since snapshot), read live ALDB first
        if not dev_obj.aldb.is_loaded or len(dev_obj.aldb) == 0:
            logger.info(f"Loading live ALDB from {clean_addr} before hardware write (live db_delta={live_delta}, cached={snap_delta})...")
            use_peek_poke = dev_obj.engine_version == EngineVersion.I1
            await self._read_aldb_robust(dev_obj.aldb, prefer_bulk=True, use_peek_poke=use_peek_poke)
            if not dev_obj.aldb.is_loaded and len(dev_obj.aldb) == 0:
                return (
                    False,
                    None,
                    f"Failed to read existing ALDB from {clean_addr} before write. Aborting to prevent overwriting slot 0x0FFF.",
                )

        return True, dev_obj, "Device ALDB ready"

    async def refresh_device_db_delta_after_write(self, address: str, snapshot: Optional[Dict[str, Any]]) -> None:
        """Query the device's updated db_delta after a hardware ALDB write and persist it into the snapshot."""
        if not self.is_connected or self._mock_mode or not PYINSTEON_AVAILABLE or not snapshot:
            return
        clean_addr = str(Address(address)).upper()
        modem = pyinsteon.devices.modem
        if modem and clean_addr == str(modem.address).upper():
            return
        ok, new_delta, _ = await self._fast_ping_and_db_delta(Address(clean_addr), timeout=4.5)
        if ok and new_delta is not None:
            for d in snapshot.get("devices", []):
                if d.get("address", "").upper() == clean_addr:
                    d["db_delta"] = int(new_delta)
                    break

    async def get_devices(self) -> List[DeviceSummary]:
        """Get summary list of all known devices (merging live pyinsteon state with snapshot metadata)."""
        if not self.is_connected:
            snapshot = self._load_snapshot()
            if snapshot:
                return self._get_mock_devices()
            if self._mock_mode or not PYINSTEON_AVAILABLE:
                return self._get_mock_devices()
            raise RuntimeError("pyinsteon is not connected. Start a session first.")

        if self._mock_mode or not PYINSTEON_AVAILABLE:
            return self._get_mock_devices()

        snapshot = self._load_snapshot()
        snap_by_addr: Dict[str, Dict[str, Any]] = {}
        if snapshot and "devices" in snapshot:
            for d in snapshot["devices"]:
                if d.get("address") and self.is_active_snapshot_device(d):
                    snap_by_addr[d["address"].upper()] = d

        results: List[DeviceSummary] = []
        seen_addrs = set()
        for addr, dev in pyinsteon.devices.items():
            addr_str = str(addr).upper()
            snap_d = snap_by_addr.get(addr_str, {})
            if dev != pyinsteon.devices.modem and addr_str not in snap_by_addr:
                raw_snap = next((x for x in (snapshot or {}).get("devices", []) if x.get("address", "").upper() == addr_str), None)
                if raw_snap and not self.is_active_snapshot_device(raw_snap):
                    continue
            seen_addrs.add(addr_str)
            live_count = len(dev.aldb) if hasattr(dev, "aldb") else 0
            snap_count = len(snap_d.get("records", []))
            desc = (
                snap_d.get("label")
                or getattr(dev, "description", "")
                or snap_d.get("description")
                or ("USB PowerLinc Modem (PLM)" if dev == pyinsteon.devices.modem else str(dev))
            )
            results.append(
                DeviceSummary(
                    address=addr_str,
                    cat=int(dev.cat or snap_d.get("cat", 0) or 0),
                    subcat=int(dev.subcat or snap_d.get("subcat", 0) or 0),
                    firmware=int(getattr(dev, "firmware", 0) or snap_d.get("firmware", 0) or 0),
                    description=desc,
                    is_battery=bool(getattr(dev, "is_battery", False) or snap_d.get("is_battery", False)),
                    aldb_record_count=live_count if live_count > 0 else snap_count,
                )
            )

        for addr_str, snap_d in snap_by_addr.items():
            if addr_str not in seen_addrs:
                results.append(
                    DeviceSummary(
                        address=addr_str,
                        cat=int(snap_d.get("cat", 0) or 0),
                        subcat=int(snap_d.get("subcat", 0) or 0),
                        firmware=int(snap_d.get("firmware", 0) or 0),
                        description=snap_d.get("label") or snap_d.get("description", "Insteon Device"),
                        is_battery=bool(snap_d.get("is_battery", False)),
                        aldb_record_count=len(snap_d.get("records", [])),
                    )
                )
        return results

    def _find_snapshot_file(self) -> Optional[str]:
        """Locate network_snapshot.json in standard directories."""
        candidates = [
            os.path.join("backend", "network_snapshot.json"),
            "network_snapshot.json",
            os.path.join("data", "network_snapshot.json"),
            os.path.join("backend", "data", "network_snapshot.json"),
            os.path.join(settings.cache_dir, "network_snapshot.json"),
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
        return None

    def _load_snapshot(self) -> Optional[Dict[str, Any]]:
        """Load network snapshot if available for realistic simulation."""
        path = self._find_snapshot_file()
        if not path:
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                logger.debug(f"Loaded live network snapshot from '{path}'.")
                return data
        except Exception as exc:
            logger.error(f"Error loading snapshot from '{path}': {exc}")
            return None

    def _get_mock_plm_info(self) -> PlmInfoResponse:
        """Provide mock data when hardware is not present for local dev/testing."""
        snapshot = self._load_snapshot()
        if snapshot and "plm" in snapshot and snapshot["plm"]:
            plm_data = snapshot["plm"]
            plm_addr = plm_data.get("address", "1A.2B.3C")
            records = [
                ALDBRecordResponse(
                    mem_addr=r.get("mem_addr", "0x0000"),
                    source_address=plm_addr,
                    target_address=r.get("target_address", ""),
                    group=r.get("group", 1),
                    is_controller=r.get("is_controller", False),
                    is_responder=r.get("is_responder", False),
                    in_use=r.get("in_use", True),
                    high_water_mark=r.get("high_water_mark", False),
                    data1=r.get("data1", 0),
                    data2=r.get("data2", 0),
                    data3=r.get("data3", 0),
                    raw_mode=r.get("raw_mode", ""),
                )
                for r in plm_data.get("records", [])
            ]
            return PlmInfoResponse(
                address=plm_addr,
                cat=plm_data.get("cat", 3),
                subcat=plm_data.get("subcat", 21),
                firmware=plm_data.get("firmware", 0),
                records_count=len(records),
                records=records,
            )

        return PlmInfoResponse(
            address="1A.2B.3C",
            cat=3,
            subcat=21,
            firmware=158,
            records_count=0,
            records=[],
        )

    def _get_mock_devices(self) -> List[DeviceSummary]:
        snapshot = self._load_snapshot()
        if snapshot and "devices" in snapshot and snapshot["devices"]:
            results: List[DeviceSummary] = []
            if "plm" in snapshot and snapshot["plm"]:
                p = snapshot["plm"]
                results.append(
                    DeviceSummary(
                        address=p.get("address", "1A.2B.3C"),
                        cat=p.get("cat", 3),
                        subcat=p.get("subcat", 21),
                        firmware=p.get("firmware", 0),
                        description="USB PowerLinc Modem (PLM)",
                        is_battery=False,
                        aldb_record_count=len(p.get("records", [])),
                    )
                )
            for d in snapshot["devices"]:
                if not self.is_active_snapshot_device(d):
                    continue
                results.append(
                    DeviceSummary(
                        address=d.get("address", ""),
                        cat=d.get("cat", 0),
                        subcat=d.get("subcat", 0),
                        firmware=d.get("firmware", 0),
                        description=d.get("label") or d.get("description", "Insteon Device"),
                        is_battery=d.get("is_battery", False),
                        aldb_record_count=len(d.get("records", [])),
                    )
                )
            return results
        return []

    def _save_snapshot(self, data: Dict[str, Any]):
        """Atomically persist network_snapshot.json to disk and keep workspace copies synchronized."""
        default_path = os.path.join("backend", "network_snapshot.json") if os.path.isdir("backend") else "network_snapshot.json"
        path = self._find_snapshot_file() or default_path
        try:
            dir_name = os.path.dirname(path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            temp_file = f"{path}.tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(temp_file, path)
            for sync_candidate in ("network_snapshot.json", os.path.join("backend", "network_snapshot.json")):
                if os.path.isfile(sync_candidate) and os.path.abspath(sync_candidate) != os.path.abspath(path):
                    with open(sync_candidate, "w", encoding="utf-8") as rf:
                        json.dump(data, rf, indent=2)
        except Exception as exc:
            logger.error(f"Error saving snapshot to '{path}': {exc}")

    def _update_device_in_snapshot(
        self,
        address: str,
        device: Any,
        live_delta: Optional[int],
        records: List[ALDBRecordResponse],
        is_loaded: bool,
    ):
        """Update or insert a single device's hardware-pulled ALDB records in network_snapshot.json."""
        snapshot = self._load_snapshot() or {"plm": {}, "devices": [], "stats": {}}
        if "devices" not in snapshot:
            snapshot["devices"] = []

        rec_dicts = [
            {
                "mem_addr": r.mem_addr,
                "target_address": r.target_address,
                "group": r.group,
                "is_controller": r.is_controller,
                "is_responder": r.is_responder,
                "in_use": r.in_use,
                "high_water_mark": r.high_water_mark,
                "data1": r.data1,
                "data2": r.data2,
                "data3": r.data3,
                "raw_mode": r.raw_mode,
            }
            for r in records
        ]

        clean_addr = address.upper().strip()
        status_str = "OK" if is_loaded else f"PARTIAL ({len(rec_dicts)} records)"
        found = False
        for d in snapshot["devices"]:
            if d.get("address", "").upper() == clean_addr:
                d["records"] = rec_dicts
                d["records_count"] = len(rec_dicts)
                if live_delta is not None:
                    d["db_delta"] = int(live_delta)
                d["status"] = status_str
                if getattr(device, "cat", 0):
                    d["cat"] = int(device.cat)
                if getattr(device, "subcat", 0):
                    d["subcat"] = int(device.subcat)
                if getattr(device, "firmware", 0):
                    d["firmware"] = int(device.firmware)
                found = True
                break

        if not found:
            cat_val = int(getattr(device, "cat", 0) or 0)
            subcat_val = int(getattr(device, "subcat", 0) or 0)
            fw_val = int(getattr(device, "firmware", 0) or 0)
            desc = str(getattr(device, "description", "") or f"Device {clean_addr}")
            snapshot["devices"].append(
                {
                    "address": clean_addr,
                    "label": desc,
                    "cat": cat_val,
                    "subcat": subcat_val,
                    "firmware": fw_val,
                    "engine_version": str(getattr(device, "engine_version", "I2CS")),
                    "db_delta": int(live_delta) if live_delta is not None else None,
                    "description": desc,
                    "is_battery": cat_val in (0x00, 0x10),
                    "status": status_str,
                    "records_count": len(rec_dicts),
                    "records": rec_dicts,
                }
            )

        self._save_snapshot(snapshot)

    async def pull_network_from_hardware(self, force_full: bool = False) -> Dict[str, Any]:
        """Crawl PLM and all discovered peer devices from physical Insteon hardware and save network_snapshot.json."""
        from datetime import datetime, timezone
        from app.openhab import openhab_client

        logger.info(f"Starting live hardware network pull (force_full={force_full})...")
        prev_snapshot = self._load_snapshot() or {}
        prev_by_addr: Dict[str, Dict[str, Any]] = {}
        for d in prev_snapshot.get("devices", []):
            if d.get("address"):
                prev_by_addr[d["address"].upper().strip()] = d

        # Fetch friendly labels and battery hints from openHAB registry if reachable
        oh_meta = await openhab_client.fetch_device_metadata()

        modem = pyinsteon.devices.modem
        if not modem:
            raise RuntimeError("No PLM modem device detected by pyinsteon.")

        if force_full and hasattr(modem, "aldb"):
            try:
                modem.aldb.clear()
            except Exception:
                pass

        logger.info(f"Reading PLM All-Link Database from hardware ({modem.address})...")
        plm_info = await self.get_plm_info(force_refresh_aldb=True)
        logger.info(f"PLM ALDB read complete: {len(plm_info.records)} records captured from {plm_info.address}.")
        plm_rec_dicts = [
            {
                "mem_addr": r.mem_addr,
                "target_address": r.target_address,
                "group": r.group,
                "is_controller": r.is_controller,
                "is_responder": r.is_responder,
                "in_use": r.in_use,
                "high_water_mark": r.high_water_mark,
                "data1": r.data1,
                "data2": r.data2,
                "data3": r.data3,
                "raw_mode": r.raw_mode,
            }
            for r in plm_info.records
        ]

        snapshot_data: Dict[str, Any] = {
            "snapshot_metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "Insteon Management Bridge Hardware Pull (Dual-Band Optimized)",
                "version": "2.0",
            },
            "plm": {
                "address": plm_info.address,
                "cat": plm_info.cat,
                "subcat": plm_info.subcat,
                "firmware": plm_info.firmware,
                "records_count": len(plm_rec_dicts),
                "records": plm_rec_dicts,
            },
            "devices": [],
            "stats": {
                "devices_total": 0,
                "devices_success": 0,
                "devices_cached_delta_match": 0,
                "devices_partial": 0,
                "devices_failed": 0,
                "devices_skipped_battery": 0,
                "total_aldb_records": len(plm_rec_dicts),
            },
        }
        self._save_snapshot(snapshot_data)

        # Discover unique target device addresses from PLM ALDB and pyinsteon.devices
        discovered_addresses = set()
        plm_cat_hints: Dict[str, tuple[int, int, int]] = {}
        plm_addr_upper = plm_info.address.upper()
        for r in plm_rec_dicts:
            t_addr = str(r.get("target_address", "")).upper().strip()
            if r.get("in_use", True) and t_addr and t_addr not in ("00.00.00", plm_addr_upper):
                discovered_addresses.add(t_addr)
                if r.get("group") == 0 and int(r.get("data1", 0)) > 0:
                    plm_cat_hints[t_addr] = (
                        int(r.get("data1", 0)),
                        int(r.get("data2", 0)),
                        int(r.get("data3", 0)),
                    )

        for addr in pyinsteon.devices:
            a_str = str(addr).upper().strip()
            if a_str and a_str != plm_addr_upper:
                discovered_addresses.add(a_str)

        sorted_addresses = sorted(discovered_addresses)
        snapshot_data["stats"]["devices_total"] = len(sorted_addresses)
        logger.info(f"Discovered {len(sorted_addresses)} unique peer devices from PLM ALDB.")

        async def _interrogate_peer(addr_str: str, is_recovery: bool = False) -> Dict[str, Any]:
            clean_compact = addr_str.replace(".", "").upper()
            meta = oh_meta.get(addr_str, {}) or oh_meta.get(clean_compact, {})
            prev_dev = prev_by_addr.get(addr_str, {})
            cat_h, subcat_h, fw_h = plm_cat_hints.get(addr_str, (0, 0, 0))
            cat_val = cat_h or int(prev_dev.get("cat", 0) or 0)
            subcat_val = subcat_h or int(prev_dev.get("subcat", 0) or 0)
            fw_val = fw_h or int(prev_dev.get("firmware", 0) or 0)

            friendly_label = meta.get("label") or prev_dev.get("label") or f"Device {addr_str}"
            is_sensor_battery = bool(meta.get("is_battery", False)) or cat_val == 0x10
            is_remote_battery = (
                (cat_val == 0x00 and subcat_val > 0)
                or ("mini keypad" in friendly_label.lower())
                or ("remote" in friendly_label.lower())
            )

            # Sleeping RF sensors (Leak/Motion/Door sensors, Cat 0x10) never answer unsolicited pings
            if is_sensor_battery and not is_remote_battery:
                logger.info(f"  -> {addr_str} ({friendly_label}): Skipping sleeping RF sensor.")
                return {
                    "address": addr_str,
                    "label": friendly_label,
                    "cat": cat_val,
                    "subcat": subcat_val,
                    "firmware": fw_val,
                    "description": meta.get("thing_type") or prev_dev.get("description") or friendly_label,
                    "is_battery": True,
                    "status": "SKIPPED_BATTERY",
                    "records_count": len(prev_dev.get("records", [])),
                    "records": prev_dev.get("records", []),
                }

            addr_obj = Address(addr_str)
            get_health(addr_obj).record_success()

            ping_ok, live_delta, on_lvl = await self._fast_ping_and_db_delta(addr_obj, timeout=4.5)
            if not ping_ok and not is_recovery:
                await asyncio.sleep(0.5)
                ping_ok, live_delta, on_lvl = await self._fast_ping_and_db_delta(addr_obj, timeout=4.5)

            if not ping_ok:
                if prev_dev.get("records"):
                    logger.info(f"  -> {addr_str} ({friendly_label}): Asleep/offline; preserving {len(prev_dev['records'])} cached ALDB records.")
                    return {
                        **prev_dev,
                        "address": addr_str,
                        "label": friendly_label,
                    }
                logger.warning(f"  -> {addr_str} ({friendly_label}): No response to Standard Ping (asleep or retired).")
                return {
                    "address": addr_str,
                    "label": friendly_label,
                    "cat": cat_val,
                    "subcat": subcat_val,
                    "firmware": fw_val,
                    "db_delta": None,
                    "description": meta.get("thing_type") or prev_dev.get("description") or friendly_label,
                    "is_battery": is_remote_battery,
                    "status": "SKIPPED_BATTERY" if is_remote_battery else "FAILED: No response to Standard Ping",
                    "records_count": 0,
                    "records": [],
                }

            delta_hex = f"0x{live_delta:02X}" if live_delta is not None else "N/A"
            logger.info(f"  -> {addr_str} ({friendly_label}): Ping OK (DB Delta: {delta_hex}, Level: {on_lvl})")

            # If not force_full and db_delta matches cached snapshot, fast-skip
            if (
                not force_full
                and prev_dev.get("status") == "OK"
                and prev_dev.get("db_delta") is not None
                and live_delta is not None
                and int(prev_dev.get("db_delta")) == int(live_delta)
                and len(prev_dev.get("records", [])) > 0
            ):
                logger.info(f"  -> {addr_str} ({friendly_label}): Fast-skipping (DB Delta {delta_hex} unchanged, {len(prev_dev['records'])} cached records).")
                return {
                    **prev_dev,
                    "address": addr_str,
                    "label": friendly_label,
                    "db_delta": int(live_delta),
                    "_cached_delta_hit": True,
                }

            device = pyinsteon.devices.get(addr_obj)
            if device is None or (int(getattr(device, "subcat", 0) or 0) == 0 and subcat_val != 0):
                try:
                    pyinsteon.devices.set_id(addr_obj, cat_val or 1, subcat_val, fw_val)
                    await asyncio.sleep(0.05)
                    device = pyinsteon.devices.get(addr_obj)
                except Exception:
                    pass
            if device is None:
                device = pyinsteon.devices.add_x10_or_insteon_device(addr_obj, cat_val or 1, subcat_val, fw_val)

            if force_full and not is_recovery and hasattr(device, "aldb"):
                try:
                    device.aldb.clear()
                except Exception:
                    pass

            if getattr(device, "engine_version", EngineVersion.UNKNOWN) == EngineVersion.UNKNOWN:
                try:
                    await asyncio.wait_for(device.async_get_engine_version(), timeout=3.5)
                except Exception:
                    self._drain_outbound_queue()

            dev_engine = getattr(device, "engine_version", EngineVersion.UNKNOWN)
            use_peek_poke = dev_engine == EngineVersion.I1
            for attempt in range(1, 4):
                get_health(addr_obj).record_success()
                prefer_bulk = (attempt == 1) and not is_recovery and len(device.aldb) == 0
                mode_desc = "10-byte Standard PEEK_POKE" if use_peek_poke else ("bulk stream" if prefer_bulk else "1-by-1 gap fill")
                logger.info(f"  -> {addr_str} ({friendly_label}): Reading ALDB Attempt {attempt}/3 ({mode_desc}, {len(device.aldb)} records in memory)...")
                try:
                    timeout_s = 65.0 if use_peek_poke else 25.0
                    await asyncio.wait_for(
                        self._read_aldb_robust(device.aldb, prefer_bulk=prefer_bulk, use_peek_poke=use_peek_poke),
                        timeout=timeout_s,
                    )
                except asyncio.TimeoutError:
                    self._drain_outbound_queue()
                if device.aldb.is_loaded:
                    break
                if len(device.aldb) == 0 and not use_peek_poke and dev_engine in (EngineVersion.I1, EngineVersion.I2):
                    use_peek_poke = True
                    logger.info(f"  -> {addr_str} ({friendly_label}): Switching to 10-byte Standard PEEK_POKE ({dev_engine.name} fallback)...")
                await asyncio.sleep(1.2 * attempt)

            parsed_recs = self._parse_aldb_records(device.aldb, source_address=addr_str)
            rec_dicts = [
                {
                    "mem_addr": r.mem_addr,
                    "target_address": r.target_address,
                    "group": r.group,
                    "is_controller": r.is_controller,
                    "is_responder": r.is_responder,
                    "in_use": r.in_use,
                    "high_water_mark": r.high_water_mark,
                    "data1": r.data1,
                    "data2": r.data2,
                    "data3": r.data3,
                    "raw_mode": r.raw_mode,
                }
                for r in parsed_recs
            ]

            if device.aldb.is_loaded:
                st_str = "OK"
                logger.info(f"  -> {addr_str} ({friendly_label}): Complete ALDB loaded ({len(rec_dicts)} records).")
            elif rec_dicts:
                st_str = f"PARTIAL ({len(rec_dicts)} records)"
                logger.warning(f"  -> {addr_str} ({friendly_label}): Partial ALDB read ({len(rec_dicts)} records).")
            else:
                st_str = "FAILED: Unresponsive to ALDB Read"
                logger.error(f"  -> {addr_str} ({friendly_label}): Failed to read ALDB (0 records).")

            dev_desc = getattr(device, "description", "") or meta.get("thing_type") or prev_dev.get("description") or friendly_label
            return {
                "address": addr_str,
                "label": friendly_label,
                "cat": int(getattr(device, "cat", 0) or cat_val),
                "subcat": int(getattr(device, "subcat", 0) or subcat_val),
                "firmware": int(getattr(device, "firmware", 0) or fw_val),
                "engine_version": str(getattr(device, "engine_version", "I2CS")),
                "db_delta": int(live_delta) if live_delta is not None else None,
                "description": str(dev_desc),
                "is_battery": is_remote_battery,
                "status": st_str,
                "records_count": len(rec_dicts),
                "records": rec_dicts,
            }

        straggler_indices: List[int] = []
        for idx, addr_str in enumerate(sorted_addresses, start=1):
            clean_compact = addr_str.replace(".", "").upper()
            meta = oh_meta.get(addr_str, {}) or oh_meta.get(clean_compact, {})
            lbl = meta.get("label") or prev_by_addr.get(addr_str, {}).get("label") or ""
            lbl_suffix = f" ({lbl})" if lbl else ""
            logger.info(f"[Hardware Pull {idx}/{len(sorted_addresses)}] Interrogating {addr_str}{lbl_suffix}...")
            entry = await _interrogate_peer(addr_str, is_recovery=False)
            dev_idx = len(snapshot_data["devices"])
            snapshot_data["devices"].append(entry)
            if entry["status"].startswith("PARTIAL"):
                straggler_indices.append(dev_idx)
            self._save_snapshot(snapshot_data)
            await asyncio.sleep(0.4)

        if straggler_indices:
            logger.info(f"[Hardware Pull Pass 2] Recovering {len(straggler_indices)} partial device(s)...")
            await asyncio.sleep(1.5)
            for dev_idx in straggler_indices:
                addr_str = snapshot_data["devices"][dev_idx]["address"]
                recovered = await _interrogate_peer(addr_str, is_recovery=True)
                if recovered["records_count"] >= snapshot_data["devices"][dev_idx]["records_count"]:
                    snapshot_data["devices"][dev_idx] = recovered
                self._save_snapshot(snapshot_data)
                await asyncio.sleep(0.4)

        for d_entry in snapshot_data["devices"]:
            st = d_entry.get("status", "")
            snapshot_data["stats"]["total_aldb_records"] += int(d_entry.get("records_count", 0))
            if st == "SKIPPED_BATTERY":
                snapshot_data["stats"]["devices_skipped_battery"] += 1
            elif st == "OK":
                snapshot_data["stats"]["devices_success"] += 1
                if d_entry.pop("_cached_delta_hit", False):
                    snapshot_data["stats"]["devices_cached_delta_match"] += 1
            elif st.startswith("PARTIAL"):
                snapshot_data["stats"]["devices_partial"] += 1
            else:
                snapshot_data["stats"]["devices_failed"] += 1

        self._save_snapshot(snapshot_data)
        logger.info(
            f"Hardware pull complete: {snapshot_data['stats']['devices_success']} OK, "
            f"{snapshot_data['stats']['devices_partial']} PARTIAL, "
            f"{snapshot_data['stats']['devices_failed']} FAILED, "
            f"{snapshot_data['stats']['total_aldb_records']} total ALDB records."
        )
        return snapshot_data

    async def get_all_network_aldb(self, force_refresh: bool = False) -> List[ALDBRecordResponse]:
        """Aggregate all ALDB records across PLM and all line devices for complete topology."""
        snapshot = self._load_snapshot()
        has_snapshot_devices = bool(snapshot and snapshot.get("devices"))

        # When connected to real hardware, perform a live hardware crawl if force_refresh=True or snapshot is empty
        if self.is_connected and not self._mock_mode and PYINSTEON_AVAILABLE and (force_refresh or not has_snapshot_devices):
            snapshot = await self.pull_network_from_hardware(force_full=force_refresh)

        all_records: List[ALDBRecordResponse] = []
        # 1. PLM records
        plm_info = await self.get_plm_info(force_refresh_aldb=False)
        all_records.extend(plm_info.records)

        # 2. Check snapshot devices if available
        if snapshot and "devices" in snapshot:
            for dev in snapshot["devices"]:
                dev_addr = dev.get("address", "")
                for r in dev.get("records", []):
                    all_records.append(
                        ALDBRecordResponse(
                            mem_addr=r.get("mem_addr", "0x0000"),
                            source_address=dev_addr,
                            target_address=r.get("target_address", ""),
                            group=r.get("group", 1),
                            is_controller=r.get("is_controller", False),
                            is_responder=r.get("is_responder", False),
                            in_use=r.get("in_use", True),
                            high_water_mark=r.get("high_water_mark", False),
                            data1=r.get("data1", 0),
                            data2=r.get("data2", 0),
                            data3=r.get("data3", 0),
                            raw_mode=r.get("raw_mode", ""),
                        )
                    )
        return all_records


    async def control_device(
        self,
        address: str,
        action: str = "on",
        level: int = 255,
        ramp_rate: Optional[int] = None,
        button: int = 1,
    ) -> bool:
        """Control a physical device live over Insteon (on, off, fast_on, fast_off, dim/set_level)."""
        clean_addr = address.strip().upper()
        action_clean = action.lower().strip()

        if self._mock_mode or not PYINSTEON_AVAILABLE or not self.is_connected:
            logger.info(f"[Mock] Triggered device {clean_addr}: action='{action_clean}', level={level}, button={button}")
            return True

        addr_obj = Address(clean_addr)
        dev = pyinsteon.devices.get(addr_obj)

        try:
            if action_clean in ("on", "set_level", "dim"):
                on_level = max(0, min(255, level))
                if dev and hasattr(dev, "async_on"):
                    res = await dev.async_on(on_level=on_level, ramp_rate=ramp_rate, button=button)
                    return res == ResponseStatus.SUCCESS
                else:
                    from pyinsteon.handlers.to_device.on_level import OnLevelCommand
                    cmd = OnLevelCommand(addr_obj)
                    res = await cmd.async_send(on_level=on_level)
                    return res == ResponseStatus.SUCCESS

            elif action_clean == "off":
                if dev and hasattr(dev, "async_off"):
                    res = await dev.async_off(button=button)
                    return res == ResponseStatus.SUCCESS
                else:
                    from pyinsteon.handlers.to_device.off import OffCommand
                    cmd = OffCommand(addr_obj)
                    res = await cmd.async_send()
                    return res == ResponseStatus.SUCCESS

            elif action_clean == "fast_on":
                if dev and hasattr(dev, "async_fast_on"):
                    res = await dev.async_fast_on(button=button)
                    return res == ResponseStatus.SUCCESS
                else:
                    from pyinsteon.handlers.to_device.fast_on import FastOnCommand
                    cmd = FastOnCommand(addr_obj)
                    res = await cmd.async_send()
                    return res == ResponseStatus.SUCCESS

            elif action_clean == "fast_off":
                if dev and hasattr(dev, "async_fast_off"):
                    res = await dev.async_fast_off(button=button)
                    return res == ResponseStatus.SUCCESS
                else:
                    from pyinsteon.handlers.to_device.fast_off import FastOffCommand
                    cmd = FastOffCommand(addr_obj)
                    res = await cmd.async_send()
                    return res == ResponseStatus.SUCCESS

            else:
                raise ValueError(f"Unknown action '{action}'. Valid actions: on, off, fast_on, fast_off, dim, set_level.")
        except Exception as exc:
            logger.error(f"Error executing {action_clean} on {clean_addr}: {exc}", exc_info=True)
            return False


insteon_manager = InsteonManager()

