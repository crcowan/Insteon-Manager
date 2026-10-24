"""Pydantic data models for Insteon Bridge API."""

from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field


class OpenhabStatusResponse(BaseModel):
    thing_uid: str
    status: str = "UNKNOWN"
    status_detail: Optional[str] = None
    is_enabled: bool = True
    reachable: bool = False


class SessionStatusResponse(BaseModel):
    is_active: bool
    locked_by: Optional[str] = None
    lease_expires_in_seconds: Optional[int] = None
    openhab_thing_enabled: bool
    pyinsteon_connected: bool
    last_heartbeat: Optional[str] = None


class SessionAcquireRequest(BaseModel):
    client_id: str = Field(default="windows-gui", description="Identifier of the client claiming lock")
    lease_seconds: Optional[int] = Field(default=None, description="Optional custom lease duration")


class ALDBRecordResponse(BaseModel):
    mem_addr: str = Field(description="Memory address hex string, e.g. '0x0fff'")
    source_address: Optional[str] = Field(default=None, description="Source device Insteon address where record is located")
    target_address: str = Field(description="Target device Insteon address, e.g. '1A.2B.3C'")
    group: int = Field(description="Group/scene number")
    is_controller: bool = Field(description="True if record is controller, False if responder")
    is_responder: bool = Field(description="True if record is responder")
    in_use: bool = Field(description="True if record is active, False if deleted")
    high_water_mark: bool = Field(description="True if high water mark bit is set")
    data1: int = Field(default=0)
    data2: int = Field(default=0)
    data3: int = Field(default=0)
    raw_mode: str = Field(default="", description="Human-readable mode (e.g. Controller/Responder)")


class DeviceSummary(BaseModel):
    address: str
    cat: int = 0
    subcat: int = 0
    firmware: int = 0
    description: str = ""
    is_battery: bool = False
    aldb_record_count: int = 0
    model_name: str = ""
    engine_version: str = ""
    read_strategy: str = ""
    write_strategy: str = ""
    is_keypad: bool = False
    button_count: int = 1
    is_dimmable: bool = True


class PlmInfoResponse(BaseModel):
    address: str
    cat: int
    subcat: int
    firmware: int
    records_count: int
    records: List[ALDBRecordResponse] = []


class GenericResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


# --- Scene Management Models ---


class SceneControllerModel(BaseModel):
    address: str
    label: str = ""
    button_group: int = 0
    is_plm: bool = False


class SceneMemberModel(BaseModel):
    address: str
    label: str = ""
    on_level: int = Field(default=255, ge=0, le=255, description="Brightness level 0-255")
    ramp_rate: int = Field(default=28, ge=0, le=255, description="Ramp rate code 0-255 (0-31 for dimmers, 255 for relays)")
    button_group: int = Field(default=1, description="Sub-button or channel 1-8")
    mem_addr: Optional[str] = Field(default=None, description="Responder ALDB slot memory address")


class SceneResponse(BaseModel):
    group: int = Field(description="Scene / All-Link Group number (1-255)")
    name: str = Field(description="Friendly name of scene")
    controllers: List[SceneControllerModel] = []
    members: List[SceneMemberModel] = []
    is_plm_controlled: bool = True


class SceneCreateRequest(BaseModel):
    group: Optional[int] = Field(default=None, ge=1, le=255, description="Scene group number (auto-allocated if omitted)")
    name: str = Field(description="Friendly name for the scene")
    controller_addresses: List[str] = Field(default=[], description="Controllers for this scene (empty defaults to PLM)")
    members: List[SceneMemberModel] = Field(default=[], description="Responder devices and their presets")


class SceneMemberUpdateRequest(BaseModel):
    on_level: Optional[int] = Field(default=None, ge=0, le=255, description="New brightness level 0-255")
    ramp_rate: Optional[int] = Field(default=None, ge=0, le=255, description="New ramp rate 0-255")


class SceneMemberAddRequest(BaseModel):
    address: str = Field(description="Responder device address to add to the scene")
    on_level: int = Field(default=255, ge=0, le=255, description="Initial brightness on-level (0-255)")
    ramp_rate: int = Field(default=28, ge=0, le=255, description="Initial ramp rate (0-31 for dimmers, 255 for relays)")
    button_group: int = Field(default=1, description="Sub-button or channel 1-8")
    controller_address: Optional[str] = Field(default=None, description="Specific controller address (defaults to all scene controllers, or PLM)")



class SceneTriggerRequest(BaseModel):
    action: str = Field(default="on", description="'on', 'off', 'fast_on', 'fast_off'")


# --- Link & Device Lifecycle Models ---


class ReciprocalLinkRequest(BaseModel):
    controller_address: str = Field(description="Controller device address (or PLM)")
    responder_address: str = Field(description="Responder device address")
    group: int = Field(default=1, ge=0, le=255, description="All-Link group (0 for PLM I2CS Auth, 1-255 for scenes/buttons)")
    data1: int = Field(default=255, ge=0, le=255, description="On-Level or Cat (0-255)")
    data2: int = Field(default=28, ge=0, le=255, description="Ramp-Rate or Subcat (0-255)")
    data3: int = Field(default=1, ge=0, le=255, description="Button/Sub-channel or Firmware (0-255)")


class ReciprocalLinkUpdateRequest(BaseModel):
    controller_address: str = Field(description="Controller device address")
    responder_address: str = Field(description="Responder device address")
    group: int = Field(default=1, ge=0, le=255, description="All-Link group")
    data1: Optional[int] = Field(default=None, ge=0, le=255, description="New On-Level / Brightness (0-255)")
    data2: Optional[int] = Field(default=None, ge=0, le=255, description="New Ramp Rate (0-255)")
    data3: Optional[int] = Field(default=None, ge=0, le=255, description="New Button/Sub-channel (0-255)")


class AldbRecordUpdateRequest(BaseModel):
    data1: Optional[int] = Field(default=None, ge=0, le=255, description="Data 1 byte (On-Level 0-255 for responders)")
    data2: Optional[int] = Field(default=None, ge=0, le=255, description="Data 2 byte (Ramp-Rate 0-255 for responders)")
    data3: Optional[int] = Field(default=None, ge=0, le=255, description="Data 3 byte (Button/sub-channel 1-8 for keypads)")


class DeviceReplaceRequest(BaseModel):
    old_address: str = Field(description="Address of decommissioned device")
    new_address: str = Field(description="Address of new replacement device")
    copy_links: bool = Field(default=True, description="Copy all scene & controller links to new device")
    sanitize_old: bool = Field(default=False, description="Attempt to erase ALDB in old device if online")


class DeviceControlRequest(BaseModel):
    action: str = Field(default="on", description="'on', 'off', 'fast_on', 'fast_off', 'dim', 'set_level'")
    level: Optional[int] = Field(default=255, ge=0, le=255, description="Brightness level (0-255)")
    ramp_rate: Optional[int] = Field(default=None, ge=0, le=255, description="Optional ramp rate")
    button: Optional[int] = Field(default=1, ge=1, le=8, description="Button/Sub-channel (1-8)")


class DeviceAddRequest(BaseModel):
    address: str = Field(description="Insteon address of new device (e.g. '4A.12.3F')")
    description: Optional[str] = Field(default="Insteon Device", description="Device friendly name or description")
    cat: Optional[int] = Field(default=1, description="Device Category (e.g. 1=Dimmable, 2=Switched)")
    subcat: Optional[int] = Field(default=0, description="Device Subcategory")
    firmware: Optional[int] = Field(default=0, description="Firmware version")
    create_default_links: bool = Field(default=True, description="Automatically establish reciprocal default links with PLM")


class MissingDefaultLinkItem(BaseModel):
    device_address: str
    device_name: str
    device_type: str = "Insteon Device"
    missing_device_to_plm: bool = False
    missing_plm_to_device: bool = False
    issue_summary: str
    recommendation: str


class DefaultLinkAuditResponse(BaseModel):
    plm_address: str
    total_devices: int
    healthy_count: int
    missing_count: int
    items: List[MissingDefaultLinkItem] = []


class NetworkIssueItem(BaseModel):
    id: str
    issue_type: str  # half_link, loopback, missing_default, ghost, duplicate, group_zero
    severity: str    # error, warning, info
    owner_address: str
    owner_name: str
    mem_addr: Optional[str] = None
    target_address: str
    target_name: str
    group: int
    role: str  # Controller or Responder
    controller_address: Optional[str] = None
    responder_address: Optional[str] = None
    summary: str
    explanation: str
    recommended_action: str  # create_reciprocal, delete_record, purge_ghost, create_default_link
    fix_label: str = "⚡ Fix Link"
    secondary_action: Optional[str] = "delete_record"
    secondary_label: Optional[str] = "🗑️ Delete Link"
    can_auto_fix: bool = True
    data1: Optional[int] = 255
    data2: Optional[int] = 28
    data3: Optional[int] = 1


class NetworkAnalysisResponse(BaseModel):
    health_score: int  # 0 to 100
    total_devices: int
    total_active_records: int
    total_issues: int
    half_links_count: int
    loopbacks_count: int
    missing_defaults_count: int
    ghosts_count: int
    duplicates_count: int
    group_zero_count: int
    issues: List[NetworkIssueItem] = []


