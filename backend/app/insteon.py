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
    from pyinsteon.aldb.aldb_record import ALDBRecord
    from pyinsteon.constants import ALDBStatus, EngineVersion, ReadWriteMode, ResponseStatus
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
            ]:
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
                # Allow 35s for full PLM EEPROM read
                await asyncio.wait_for(modem.aldb.async_load(), timeout=35.0)
            except asyncio.TimeoutError:
                logger.warning("Timeout loading PLM ALDB from hardware; returning cached records.")

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

    async def _fast_ping_and_db_delta(self, addr_obj, timeout: float = 6.0):
        """Send a 10-byte Standard StatusRequest (0x19) to verify reachability and capture db_delta."""
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        cmd = StatusRequestCommand(addr_obj, status_type=0)

        def _on_status(db_delta, status):
            if not fut.done():
                fut.set_result((int(db_delta), int(status)))

        cmd.subscribe(_on_status)
        try:
            send_Task = asyncio.create_task(cmd.async_send())
            res = await asyncio.wait_for(fut, timeout=timeout)
            try:
                await asyncio.wait_for(send_Task, timeout=1.0)
            except Exception:
                pass
            return True, res[0], res[1]
        except Exception:
            return False, None, None
        finally:
            try:
                cmd.unsubscribe(_on_status)
            except Exception:
                pass

    async def _fast_peek_record(self, aldb, mem_addr: int):
        """Read a single 8-byte ALDB record using 10-byte Standard PEEK_POKE (I1/I2 fallback)."""
        addr_obj = aldb.address
        msb_cmd = SetMSBAddressCommand(addr_obj)
        peek_cmd = PeekCommand(addr_obj)
        current_msb = None

        async def _peek_one_byte(byte_addr: int):
            nonlocal current_msb
            msb = byte_addr >> 8
            lsb = byte_addr & 0xFF
            loop = asyncio.get_running_loop()

            if msb != current_msb:
                msb_fut = loop.create_future()

                def _on_msb(high_byte):
                    if not msb_fut.done():
                        msb_fut.set_result(high_byte)

                msb_cmd.subscribe(_on_msb)
                try:
                    t = asyncio.create_task(msb_cmd.async_send(high_byte=msb))
                    await asyncio.wait_for(msb_fut, timeout=4.0)
                    current_msb = msb
                    try:
                        await asyncio.wait_for(t, timeout=0.5)
                    except Exception:
                        pass
                except Exception:
                    return None
                finally:
                    try:
                        msb_cmd.unsubscribe(_on_msb)
                    except Exception:
                        pass

            val_fut = loop.create_future()

            def _on_peek(lsb_addr, value):
                if not val_fut.done():
                    val_fut.set_result(int(value))

            peek_cmd.subscribe(_on_peek)
            try:
                t = asyncio.create_task(peek_cmd.async_send(lsb=lsb))
                val = await asyncio.wait_for(val_fut, timeout=4.0)
                try:
                    await asyncio.wait_for(t, timeout=0.5)
                except Exception:
                    pass
                return val
            except Exception:
                return None
            finally:
                try:
                    peek_cmd.unsubscribe(_on_peek)
                except Exception:
                    pass

        ctrl = await _peek_one_byte(mem_addr - 7)
        if ctrl is None:
            return None

        is_in_use = bool(ctrl & 0x80)
        is_controller = bool(ctrl & 0x40)
        is_bit5 = bool(ctrl & 0x20)
        is_bit4 = bool(ctrl & 0x10)
        is_hwm = not bool(ctrl & 0x02)

        if is_hwm:
            return ALDBRecord(
                memory=mem_addr,
                control_flags=ctrl,
                group=0,
                target=Address("00.00.00"),
                data1=0,
                data2=0,
                data3=0,
            )

        if not is_in_use:
            return ALDBRecord(
                memory=mem_addr,
                control_flags=ctrl,
                group=0,
                target=Address("00.00.00"),
                data1=0,
                data2=0,
                data3=0,
                in_use=False,
                high_water_mark=False,
            )

        rest = []
        for offset in range(6, -1, -1):
            b = await _peek_one_byte(mem_addr - offset)
            if b is None:
                return None
            rest.append(b)

        group, id_hi, id_mid, id_lo, d1, d2, d3 = rest
        return ALDBRecord(
            memory=mem_addr,
            controller=is_controller,
            group=group,
            target=Address(bytearray([id_hi, id_mid, id_lo])),
            data1=d1,
            data2=d2,
            data3=d3,
            in_use=is_in_use,
            high_water_mark=is_hwm,
            bit5=is_bit5,
            bit4=is_bit4,
        )

    async def _read_aldb_robust(self, aldb, prefer_bulk: bool = True, use_peek_poke: bool = False) -> ALDBStatus:
        """Read missing ALDB records using bulk stream + 1-by-1 gap filling or 10-byte PEEK_POKE."""
        if use_peek_poke:
            aldb._read_write_mode = ReadWriteMode.PEEK_POKE
            addr = aldb.first_mem_addr
            consecutive_failures = 0
            while addr >= 0x0000:
                existing = aldb.get(addr)
                if existing is not None:
                    if existing.is_high_water_mark:
                        aldb._update_status(ALDBStatus.LOADED)
                        return aldb.status
                    addr -= 8
                    continue
                rec = await self._fast_peek_record(aldb, addr)
                if rec is None:
                    consecutive_failures += 1
                    if consecutive_failures >= 2:
                        break
                    await asyncio.sleep(0.5)
                    continue
                consecutive_failures = 0
                aldb._records[rec.mem_addr] = rec
                if rec.is_high_water_mark:
                    aldb._update_status(ALDBStatus.LOADED)
                    return aldb.status
                addr -= 8
                await asyncio.sleep(0.05)

            for r in aldb._records.values():
                if r.is_high_water_mark:
                    aldb._update_status(ALDBStatus.LOADED)
                    return aldb.status
            if aldb._records:
                aldb._update_status(ALDBStatus.PARTIAL)
            else:
                aldb._update_status(ALDBStatus.FAILED)
            return aldb.status

        if prefer_bulk and len(aldb) == 0:
            async for rec in aldb._read_manager.async_read_all():
                aldb._records[rec.mem_addr] = rec
                if aldb._cb_aldb_loaded:
                    aldb._cb_aldb_loaded()
                if rec.is_high_water_mark:
                    break

        for r in aldb._records.values():
            if r.is_high_water_mark and aldb._is_loaded():
                aldb._update_status(ALDBStatus.LOADED)
                return aldb.status

        consecutive_failures = 0
        for mem_addr in aldb._missing_records():
            got_rec = False
            async for rec in aldb._read_manager.async_read_one(mem_addr):
                aldb._records[rec.mem_addr] = rec
                got_rec = True
                consecutive_failures = 0
                if aldb._cb_aldb_loaded:
                    aldb._cb_aldb_loaded()
                if rec.is_high_water_mark:
                    break
            if not got_rec:
                consecutive_failures += 1
                if consecutive_failures >= 2:
                    break
            for r in aldb._records.values():
                if r.is_high_water_mark:
                    break
            else:
                await asyncio.sleep(0.15)
                continue
            break

        if aldb._is_loaded():
            aldb._update_status(ALDBStatus.LOADED)
        elif aldb._records:
            aldb._update_status(ALDBStatus.PARTIAL)
        else:
            aldb._update_status(ALDBStatus.FAILED)
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
        ping_ok, live_delta, _ = await self._fast_ping_and_db_delta(addr_obj, timeout=6.0)
        if not ping_ok:
            if cached_dev and cached_dev.get("records"):
                logger.info(f"Device {clean_addr} asleep/offline; returning {len(cached_dev['records'])} cached ALDB records.")
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
            return []

        if (
            not force_refresh
            and cached_dev
            and cached_dev.get("status") == "OK"
            and cached_dev.get("db_delta") is not None
            and int(cached_dev.get("db_delta")) == live_delta
            and cached_dev.get("records")
        ):
            logger.info(f"Fast-skipping {clean_addr}: db_delta 0x{live_delta:02X} unchanged.")
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
            device = pyinsteon.devices.add_x10_or_insteon_device(addr_obj, 0x01, 0x00, 0x00)
        if device.engine_version == EngineVersion.UNKNOWN:
            try:
                eng_cmd = EngineVersionRequest(addr_obj)
                _, eng_ver = await asyncio.wait_for(eng_cmd.async_send(), timeout=5.0)
                if eng_ver in (EngineVersion.I1, EngineVersion.I2, EngineVersion.I2CS):
                    device.engine_version = eng_ver
            except Exception:
                pass
            if device.engine_version == EngineVersion.UNKNOWN:
                device.engine_version = EngineVersion.I2CS

        use_peek_poke = device.engine_version == EngineVersion.I1
        for attempt in range(1, 4):
            prefer_bulk = attempt == 1 and len(device.aldb) == 0
            try:
                timeout_s = 65.0 if use_peek_poke else 22.0
                await asyncio.wait_for(
                    self._read_aldb_robust(device.aldb, prefer_bulk=prefer_bulk, use_peek_poke=use_peek_poke),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                pass
            if device.aldb.is_loaded:
                break
            if len(device.aldb) == 0 and not use_peek_poke and device.engine_version in (EngineVersion.I1, EngineVersion.I2):
                use_peek_poke = True
            await asyncio.sleep(1.5 * attempt)

        return self._parse_aldb_records(device.aldb, source_address=clean_addr)

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

        seeded = 0
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
                aldb._records[mem_int] = rec
                seeded += 1
            except Exception as ex:
                logger.debug(f"Skipping invalid snapshot record during ALDB seed: {ex}")

        # Ensure non-modem ALDB has a High-Water Mark record at the end so pyinsteon knows where the table terminates
        if not is_modem and seeded > 0 and not has_hwm:
            hwm_addr = max(0x0000, min_mem - 8)
            aldb._records[hwm_addr] = ALDBRecord(
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

        if seeded > 0:
            try:
                aldb._update_status(ALDBStatus.LOADED)
            except Exception:
                pass
        return seeded

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
                logger.info(f"Loaded live network snapshot from '{path}'.")
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

        mock_records = [
            ALDBRecordResponse(
                mem_addr="0x0FFF",
                source_address="1A.2B.3C",
                target_address="2A.44.11",
                group=1,
                is_controller=True,
                is_responder=False,
                in_use=True,
                high_water_mark=False,
                data1=0,
                data2=0,
                data3=0,
                raw_mode="Controller",
            ),
            ALDBRecordResponse(
                mem_addr="0x0FF7",
                source_address="1A.2B.3C",
                target_address="3C.55.99",
                group=1,
                is_controller=False,
                is_responder=True,
                in_use=True,
                high_water_mark=False,
                data1=255,
                data2=28,
                data3=1,
                raw_mode="Responder",
            ),
            ALDBRecordResponse(
                mem_addr="0x0FEF",
                source_address="1A.2B.3C",
                target_address="12.34.56",
                group=2,
                is_controller=True,
                is_responder=False,
                in_use=True,
                high_water_mark=True,
                data1=0,
                data2=0,
                data3=0,
                raw_mode="Controller (High Water Mark)",
            ),
        ]
        return PlmInfoResponse(
            address="1A.2B.3C",
            cat=3,
            subcat=21,
            firmware=158,
            records_count=len(mock_records),
            records=mock_records,
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

        return [
            DeviceSummary(
                address="1A.2B.3C",
                cat=3,
                subcat=21,
                firmware=158,
                description="USB PowerLinc Modem (PLM)",
                is_battery=False,
                aldb_record_count=3,
            ),
            DeviceSummary(
                address="2A.44.11",
                cat=2,
                subcat=42,
                firmware=65,
                description="Dimmer Switch 2477D",
                is_battery=False,
                aldb_record_count=5,
            ),
            DeviceSummary(
                address="3C.55.99",
                cat=2,
                subcat=54,
                firmware=70,
                description="KeypadLinc 6-Button 2487S",
                is_battery=False,
                aldb_record_count=8,
            ),
        ]

    async def get_all_network_aldb(self) -> List[ALDBRecordResponse]:
        """Aggregate all ALDB records across PLM and all line devices for complete topology."""
        all_records: List[ALDBRecordResponse] = []
        # 1. PLM records
        plm_info = await self.get_plm_info()
        all_records.extend(plm_info.records)

        # 2. Check snapshot devices if available
        snapshot = self._load_snapshot()
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

