"""Insteon Device Hardware Capability Registry and Fingerprinting Engine.

Provides deterministic hardware profiles, category/subcategory taxonomies,
and silicon I/O strategy resolution across Insteon hardware generations.
"""

from dataclasses import dataclass
from enum import Enum
import logging
from typing import Dict, Optional, Tuple, Any

logger = logging.getLogger("insteon.registry")


class EngineVersionStr(str, Enum):
    I1 = "i1"
    I2 = "i2"
    I2CS = "i2cs"
    I3 = "i3"
    UNKNOWN = "unknown"


class ReadStrategy(str, Enum):
    STANDARD = "STANDARD"  # Extended Read (0x2F) for modern I2CS / 4KB EEPROM devices
    PEEK = "PEEK"          # Standard 10-byte PEEK (0x28) exclusively (Legacy I1 devices)
    HYBRID = "HYBRID"      # Extended Write (0x2F) + Fast PEEK (0x28) Read (Early I2 devices, e.g. LampLinc 2457D2)


class WriteStrategy(str, Enum):
    STANDARD = "STANDARD"  # Extended Write (0x2F 0x02)
    PEEK_POKE = "PEEK_POKE" # Standard 10-byte POKE (0x2B 0x01) (Legacy I1 devices)


@dataclass(frozen=True)
class DeviceHardwareProfile:
    """Authoritative hardware capability profile for an Insteon silicon device."""
    address: str
    cat: int
    subcat: int
    firmware: int
    engine_version: str
    is_battery: bool
    is_keypad: bool
    button_count: int
    is_dimmable: bool
    read_strategy: ReadStrategy
    write_strategy: WriteStrategy
    model_name: str
    description: str


# Authoritative hardware fingerprint registry for known network devices
KNOWN_DEVICE_PROFILES: Dict[str, Dict[str, Any]] = {
    # USB PowerLinc Modem (PLM 2413U) - Engine I2CS, Cat 0x03, Subcat 0x15
    "53.C1.42": {
        "cat": 3,
        "subcat": 21,
        "firmware": 158,
        "engine_version": "i2cs",
        "is_battery": False,
        "is_keypad": False,
        "button_count": 1,
        "is_dimmable": False,
        "read_strategy": ReadStrategy.STANDARD,
        "write_strategy": WriteStrategy.STANDARD,
        "model_name": "2413U",
        "description": "USB PowerLinc Modem (PLM)",
    },
    # LampLinc Dual-Band Dimmer (2457D2) - Engine I2, Cat 0x01, Subcat 0x0E (14)
    # CRITICAL: Suffers from "I2 Read Trap" where single-record 0x2F reads hang; requires HYBRID mode
    "18.96.F5": {
        "cat": 1,
        "subcat": 14,
        "firmware": 59,
        "engine_version": "i2",
        "is_battery": False,
        "is_keypad": False,
        "button_count": 1,
        "is_dimmable": True,
        "read_strategy": ReadStrategy.HYBRID,
        "write_strategy": WriteStrategy.STANDARD,
        "model_name": "2457D2",
        "description": "LampLinc Dimmer (Samavar Light)",
    },
    # Mini Keypad 4-Scene (2342-242) - Battery Powered Wireless Remote
    # CRITICAL: Asynchronous sleep; RF receiver powered off 99.9% of the time
    "1C.2C.40": {
        "cat": 0,
        "subcat": 26,
        "firmware": 0,
        "engine_version": "i2",
        "is_battery": True,
        "is_keypad": True,
        "button_count": 4,
        "is_dimmable": False,
        "read_strategy": ReadStrategy.STANDARD,
        "write_strategy": WriteStrategy.STANDARD,
        "model_name": "2342-242",
        "description": "Mini Keypad (4-Scene Remote)",
    },
    # Front Hall Light (23.BA.1F) - Legacy Engine 1 Device
    # CRITICAL: Standard PEEK/POKE only; does not support extended 0x2F messages
    "23.BA.1F": {
        "cat": 1,
        "subcat": 0,
        "firmware": 0,
        "engine_version": "i1",
        "is_battery": False,
        "is_keypad": False,
        "button_count": 1,
        "is_dimmable": True,
        "read_strategy": ReadStrategy.PEEK,
        "write_strategy": WriteStrategy.PEEK_POKE,
        "model_name": "2476D",
        "description": "SwitchLinc Dimmer (Front Hall Light)",
    },
    # Dimmers in the network (SwitchLinc 2477D / Dual-Band Dimmers)
    "15.88.E3": {
        "cat": 1,
        "subcat": 32,
        "firmware": 56,
        "engine_version": "i2",
        "is_battery": False,
        "is_keypad": False,
        "button_count": 1,
        "is_dimmable": True,
        "read_strategy": ReadStrategy.STANDARD,
        "write_strategy": WriteStrategy.STANDARD,
        "model_name": "2477D",
        "description": "SwitchLinc Dimmer (Terrace Lights)",
    },
    "15.89.BA": {
        "cat": 1,
        "subcat": 32,
        "firmware": 56,
        "engine_version": "i2",
        "is_battery": False,
        "is_keypad": False,
        "button_count": 1,
        "is_dimmable": True,
        "read_strategy": ReadStrategy.STANDARD,
        "write_strategy": WriteStrategy.STANDARD,
        "model_name": "2477D",
        "description": "SwitchLinc Dimmer (Hall Copper Light)",
    },
    "18.9A.A2": {
        "cat": 1,
        "subcat": 32,
        "firmware": 0,
        "engine_version": "i2",
        "is_battery": False,
        "is_keypad": False,
        "button_count": 1,
        "is_dimmable": True,
        "read_strategy": ReadStrategy.STANDARD,
        "write_strategy": WriteStrategy.STANDARD,
        "model_name": "2477D",
        "description": "SwitchLinc Dimmer (CRC Light)",
    },
    "1A.EB.C5": {
        "cat": 1,
        "subcat": 32,
        "firmware": 0,
        "engine_version": "i2",
        "is_battery": False,
        "is_keypad": False,
        "button_count": 1,
        "is_dimmable": True,
        "read_strategy": ReadStrategy.STANDARD,
        "write_strategy": WriteStrategy.STANDARD,
        "model_name": "2477D",
        "description": "SwitchLinc Dimmer (Dining Room Spots)",
    },
    "1D.C1.50": {
        "cat": 1,
        "subcat": 32,
        "firmware": 0,
        "engine_version": "i2",
        "is_battery": False,
        "is_keypad": False,
        "button_count": 1,
        "is_dimmable": True,
        "read_strategy": ReadStrategy.STANDARD,
        "write_strategy": WriteStrategy.STANDARD,
        "model_name": "2477D",
        "description": "SwitchLinc Dimmer (Train Lights)",
    },
    "1D.C5.C2": {
        "cat": 1,
        "subcat": 32,
        "firmware": 0,
        "engine_version": "i2",
        "is_battery": False,
        "is_keypad": False,
        "button_count": 1,
        "is_dimmable": True,
        "read_strategy": ReadStrategy.STANDARD,
        "write_strategy": WriteStrategy.STANDARD,
        "model_name": "2477D",
        "description": "SwitchLinc Dimmer (Terrace Bookcase)",
    },
    "23.C1.2C": {
        "cat": 1,
        "subcat": 32,
        "firmware": 0,
        "engine_version": "i2",
        "is_battery": False,
        "is_keypad": False,
        "button_count": 1,
        "is_dimmable": True,
        "read_strategy": ReadStrategy.STANDARD,
        "write_strategy": WriteStrategy.STANDARD,
        "model_name": "2477D",
        "description": "SwitchLinc Dimmer (Main Bookcase)",
    },
    "3C.6E.04": {
        "cat": 1,
        "subcat": 32,
        "firmware": 0,
        "engine_version": "i2",
        "is_battery": False,
        "is_keypad": False,
        "button_count": 1,
        "is_dimmable": True,
        "read_strategy": ReadStrategy.STANDARD,
        "write_strategy": WriteStrategy.STANDARD,
        "model_name": "2477D",
        "description": "SwitchLinc Dimmer (Magic Lights)",
    },
    "3C.75.99": {
        "cat": 1,
        "subcat": 32,
        "firmware": 0,
        "engine_version": "i2",
        "is_battery": False,
        "is_keypad": False,
        "button_count": 1,
        "is_dimmable": True,
        "read_strategy": ReadStrategy.STANDARD,
        "write_strategy": WriteStrategy.STANDARD,
        "model_name": "2477D",
        "description": "SwitchLinc Dimmer (Office Small Lamp)",
    },
    "3E.91.E6": {
        "cat": 1,
        "subcat": 32,
        "firmware": 0,
        "engine_version": "i2",
        "is_battery": False,
        "is_keypad": False,
        "button_count": 1,
        "is_dimmable": True,
        "read_strategy": ReadStrategy.STANDARD,
        "write_strategy": WriteStrategy.STANDARD,
        "model_name": "2477D",
        "description": "SwitchLinc Dimmer (Dining Room Small Lamp)",
    },
}

# Subcategories known to be KeypadLincs (with 6 or 8 buttons)
KEYPAD_SUBCATS = {0x09, 0x0B, 0x0C, 0x0D, 0x1A, 0x1B, 0x1E, 0x22}

# Battery category codes: 0x00 (Controllers/Remotes) and 0x10 (Sensors)
BATTERY_CATS = {0x00, 0x10}


def get_device_hardware_profile(
    address: str,
    cat: Optional[int] = None,
    subcat: Optional[int] = None,
    firmware: Optional[int] = None,
    engine_version: Optional[str] = None,
    label: Optional[str] = None,
) -> DeviceHardwareProfile:
    """Resolve the definitive hardware profile for an Insteon device address.

    Merges known hardware fingerprints with category/engine deduction rules.
    """
    clean_addr = address.strip().upper()

    # 1. Check authoritative known device registry
    if clean_addr in KNOWN_DEVICE_PROFILES:
        info = KNOWN_DEVICE_PROFILES[clean_addr]
        return DeviceHardwareProfile(
            address=clean_addr,
            cat=int(cat if cat is not None and cat > 0 else info["cat"]),
            subcat=int(subcat if subcat is not None and subcat > 0 else info["subcat"]),
            firmware=int(firmware if firmware is not None and firmware > 0 else info["firmware"]),
            engine_version=str(engine_version if engine_version and engine_version != "unknown" else info["engine_version"]),
            is_battery=bool(info["is_battery"]),
            is_keypad=bool(info["is_keypad"]),
            button_count=int(info.get("button_count", 1)),
            is_dimmable=bool(info.get("is_dimmable", True)),
            read_strategy=info["read_strategy"],
            write_strategy=info["write_strategy"],
            model_name=info["model_name"],
            description=info["description"],
        )

    # 2. Heuristic resolution for newly discovered devices
    eff_cat = int(cat or 1)
    eff_subcat = int(subcat or 0)
    eff_fw = int(firmware or 0)
    lbl = (label or "").lower()

    is_batt = eff_cat in BATTERY_CATS or any(k in lbl for k in ("keypad", "remote", "sensor", "motion", "leak", "door"))
    is_kp = eff_subcat in KEYPAD_SUBCATS or "keypad" in lbl
    btn_count = 8 if (is_kp and "8" in lbl) else (6 if (is_kp and "6" in lbl) else (4 if "4" in lbl else (8 if is_kp else 1)))
    is_dim = eff_cat == 1

    eng_str = str(engine_version or "unknown").lower()
    if eng_str in ("i1", "engine1", "1"):
        read_strat = ReadStrategy.PEEK
        write_strat = WriteStrategy.PEEK_POKE
        eng = "i1"
    elif eff_cat == 1 and eff_subcat == 14:  # LampLinc 2457D2
        read_strat = ReadStrategy.HYBRID
        write_strat = WriteStrategy.STANDARD
        eng = "i2"
    elif eng_str in ("i2cs", "2cs"):
        read_strat = ReadStrategy.STANDARD
        write_strat = WriteStrategy.STANDARD
        eng = "i2cs"
    else:
        read_strat = ReadStrategy.HYBRID if is_dim else ReadStrategy.STANDARD
        write_strat = WriteStrategy.STANDARD
        eng = "i2"

    return DeviceHardwareProfile(
        address=clean_addr,
        cat=eff_cat,
        subcat=eff_subcat,
        firmware=eff_fw,
        engine_version=eng,
        is_battery=is_batt,
        is_keypad=is_kp,
        button_count=btn_count,
        is_dimmable=is_dim,
        read_strategy=read_strat,
        write_strategy=write_strat,
        model_name="Generic Insteon Device",
        description=label or f"Device {clean_addr}",
    )
