# Insteon Manager: Network Topology, Diagnostics & ALDB Management Suite

An enterprise-grade, decoupled management suite for Insteon dual-mesh home automation networks. Designed to bypass the operational and diagnostic limitations of generic smart-home platforms while guaranteeing physical EEPROM integrity, high network performance, and rock-solid system stability.

---

## 🏛️ Key Architectural Decisions & Engineering Principles

### 1. Host-Agnostic Hardware Arbitration (openHAB & Beyond)
Generic smart home hubs (openHAB, Home Assistant, etc.) lock serial interfaces continuously, making concurrent deep diagnostics impossible without port collision.
* **Management Session Lease:** Rather than thrashing the serial port per command, Insteon Manager uses a stateful **Management Session**. Upon acquisition, the bridge safely pauses host bindings via REST (e.g. openHAB Insteon Thing `PUT /rest/things/{uid}/enable false`), transfers ownership of the USB PLM (`/dev/ttyUSB0`) to `pyinsteon`, and begins high-speed management.
* **Watchdog Keep-Alive:** The desktop client issues periodic heartbeats (30s interval). If the client disconnects or crashes, an autonomous backend watchdog releases the serial port and restores the host automation engine to ONLINE within 300 seconds.
* **Modular Adaptability:** While pre-configured for openHAB 4.x/5.x, the host arbitration adapter is completely decoupled. By implementing a lightweight adapter class, it can easily manage **Home Assistant** (suspending the Insteon integration via WebSocket/REST), **Node-RED**, or run as a standalone **Headless Network Controller**.

### 2. Protected Core System Links (Groups 0 & 1)
Insteon networks rely on two foundational link types that standard tools frequently misdiagnose or corrupt:
* **Group 0 (I2CS Authorization & Direct Control):** Modern I2CS devices require mutual Group 0 records with the PLM for payload encryption and configuration rights.
* **Group 1 (Primary Switch Load & Status Reporting):** When a wall switch paddle is tapped, it broadcasts on Group 1. The PLM must be linked as a Group 1 responder to receive instant physical state changes.
* **Hardware-Enforced Protection:** The suite strictly guards these core communication links (`🔒 Core Hub Link`). The UI disables their deletion, the Health Analyzer suppresses false duplicate warnings on them, and automated batch cleanups will never sever them.

### 3. Unified Pre-Commit Safety Pipeline (`ChangeSetService`), Two-Phase Commits & Undo Stack
Powerline and RF dual-mesh networks are subject to noise, attenuation, and packet collisions. Optimistic databases frequently drift from physical reality.
* **Unified Staging & Preflight Verification:** Every structural network modification—link additions/deletions, device additions/replacements, scene membership edits, ghost target purges, PLM default link repairs, and Health Analyzer fixes—routes into the **Slide-Over Pending Changes Drawer** for preflight review before touching physical hardware.
* **Preflight Impact & Battery Sensor Analysis:** Automatically calculates total EEPROM write operations, estimated powerline duration, risk severity, and flags **battery-operated RF sensors** (Motion, Leak, Door/Window sensors) that must be manually woken using their physical `SET` button prior to committing.
* **2-Phase Hardware Verification & Rollback:** Every commit writes to device EEPROM and immediately executes a physical read-back (`_clean_peek` with powerline echo dissipation). If the responder fails verification, the controller link is rolled back to prevent half-links.
* **Session Undo Stack (`↩ Undo`):** Live preset adjustments and link modifications record their prior EEPROM state, allowing 1-click staging of an inverse EEPROM write to revert the last change.
* **Zero Database Fantasy:** The database snapshot is only updated once the physical silicon confirms the byte sequence.

### 4. High-Performance Network Maintenance & Surgical EEPROM Tuning
* **Surgical Preset Adjustment:** Adjusting a responder's On-Level (brightness) or Ramp Rate (fade speed) modifies *only* that responder's specific 8-byte EEPROM record. Other scene members and controllers remain untouched, eliminating the risk and multi-minute traffic of full scene rebuilds.
* **Device × Scene Participation Matrix:** Provides a high-density cross-reference of every Insteon device across all active scenes with `[C]` Controller and `[R · %]` Responder role chips and 1-click navigation into the Scene Preset Tuner.
* **Device Replacement Wizard ("Clone & Swap"):** Replacing a broken switch historically required manually rebuilding dozens of scenes. The Clone & Swap engine reads the legacy device's records, writes reciprocal links to the replacement switch, and systematically retargets every peer switch and keypad in the house to point to the new address in a single coordinated operation.

### 5. Heuristic Network Health Analyzer (7-Step Diagnostics)
A comprehensive diagnostic engine inspects the entire network topology against an authoritative rule-base:
1. **Ghost Targets:** Eradicates dangling link records pointing to retired/unplugged hardware (eliminating wasted retransmit retries that degrade network responsiveness).
2. **Missing Core Links:** Detects unlinked devices missing critical Group 0 (management) or Group 1 (status reporting) links with the PLM.
3. **Loopbacks:** Identifies corrupted records where a device targets its own address.
4. **Duplicate EEPROM Slots:** Detects redundant identical link records consuming limited EEPROM space.
5. **Half-Links:** Surfaces one-way links where a controller has no reciprocal responder (or vice-versa), offering 1-click staged reciprocal repair.
6. **Corrupted Group 0 Records:** Cleans invalid Group 0 records on non-PLM devices.
7. **Scene Health:** Flags obsolete diagnostic artifacts (e.g., Group 251 engine pings), empty zombie scenes (controllers with 0 responders), and single-device redundant scenes with 1-click staged cleanup.

---

## 🏗️ System Architecture

```
 +-------------------------------------------------------------------------+
 |                           Windows Desktop                               |
 |                                                                         |
 |   Insteon Network Studio (.NET 10 WPF / MVVM)                           |
 |   ├── Top Telemetry Header (Mode Badge, Health Score Pill, Counts)      |
 |   ├── Workspace 1: 🌐 Network Overview (Interactive Topology & Node UI) |
 |   ├── Workspace 2: 🔌 Devices & Links (Inspector, Raw ALDB, Link Tools) |
 |   ├── Workspace 3: 🎭 Scenes & Presets (Preset Tuner & Device Matrix)   |
 |   ├── Workspace 4: ♥ Health & Maintenance (Analyzer, PLM Audit, Ghosts) |
 |   ├── Slide-Over Drawer: ⚖️ Pending Changes, Preflight & Undo History   |
 |   └── Collapsible Bottom Drawer: 📜 Live Event & Powerline Bridge Log   |
 +------------------------------------+------------------------------------+
                                      |  REST (HTTP JSON)
                                      |  Heartbeat Keep-Alive (30s)
                                      v
 +-------------------------------------------------------------------------+
 |                      Raspberry Pi 4 / Linux Host                        |
 |                                                                         |
 |   FastAPI Bridge Service (/opt/insteon-bridge)                          |
 |   ├── Hardware Session & Watchdog Arbitrator                            |
 |   ├── Host Automation Adapter (openHAB / Home Assistant / Standalone)   |
 |   ├── Atomic 2-Phase Transaction Manager (Commit & Rollback)            |
 |   ├── Scene Service & Hardware Crawler                                  |
 |   └── pyinsteon Asynchronous Core Driver                                |
 |              |                                 |                        |
 |   [ REST API Calls ]                  [ Serial /dev/ttyUSB0 ]           |
 |              v                                 v                        |
 |   +----------------------+           +--------------------+             |
 |   | Host Automation Hub  |           | Hardware USB PLM   |             |
 |   | (openHAB 5 / HA)     |           | (2413U / 53.C1.42) |             |
 |   +----------------------+           +---------+----------+             |
 +------------------------------------------------|------------------------+
                                                  | Insteon Dual-Mesh
                                                  v (Powerline + RF)
                             +--------------------+--------------------+
                             |                                         |
                             v                                         v
                 [ Wall Switches & Dimmers ]               [ Keypads & Plug-in Modules ]
```

---

## 🖥️ Studio Workspaces & User Interface Architecture

**Insteon Network Studio** separates **Browse Mode** (`👁️ BROWSE MODE (Read-Only Cache)` — full exploration and change staging while openHAB stays `ONLINE`) from **Manage Mode** (`🛠️ MANAGE MODE (Live Hardware Exclusive)` — exclusive PLM hardware lock for live powerline commands and verified EEPROM commits, with automatic escalation prompts when needed).

### 4 Primary Workspaces
| Workspace | Sub-Views & Capabilities | Description |
|---|---|---|
| **1. `🌐 Network Overview`** | **Interactive Topology Map & Node Inspector Sidebar** | Force-directed / hierarchical node-and-link canvas with link reciprocity visualization, hardware identity ribbons, and 1-click navigation to Device Inspector or Link Tools. |
| **2. `🔌 Devices & Links`** | **• `🗂️ Organized Device Inspector`**<br>**• `📋 Raw ALDB Records Table`**<br>**• `🔗 Add, Replace & Link Tools`** | Categorizes Controller vs. Responder links with **`🔒 Core Hub Link`** protection badges, progressive disclosure (**`🔧 Show Raw ALDB Details`**), searchable raw EEPROM records, new device provisioning, **"Clone & Swap" Device Replacement Wizard**, and atomic reciprocal link creation/preset tuning/deletion. |
| **3. `🎭 Scenes & Presets`** | **• `🎛️ Scene Preset Tuner`**<br>**• `📊 Device × Scene Matrix`** | Manage multi-device scenes, test live broadcasts, surgically tune individual responder On-Level brightness and Ramp Rate transitions, or switch to the high-density **Device × Scene Participation Matrix**. |
| **4. `♥ Health & Maintenance`** | **• `♥ Network Health Analyzer`**<br>**• `🛡️ PLM Link Audit & Ghost Cleaner`** | 7-step diagnostic engine scoring network integrity (`0–100%`) with 6-category anomaly breakdown, battery-device wake alerts, PLM Group 1 self-healing audit, and network-wide Ghost Target eradication. |

### Persistent Contextual Drawers & Telemetry Bar
| Component | Location | Description |
|---|---|---|
| **`♥ Network Health Pill` & Telemetry Counters** | Top Command Bar (Left) | Always-visible health score (`100% — Healthy`) and live Device, Scene, and ALDB Record counts; clicking the Health pill jumps directly to `♥ Health & Maintenance`. |
| **`⚖️ Pending Changes & Verification` Drawer** | Slide-Over Right Drawer | Opens automatically when any change is staged (or via the top-bar badge). Displays the **Preflight Safety & Impact Banner**, risk badges, current vs. proposed EEPROM state diffs, **`↩ Undo`**, **`❌ Discard All`**, and **`✔️ Commit to Hardware`**. |
| **`📜 Live Event & Bridge Log` Drawer** | Collapsible Bottom Drawer | Toggleable from the bottom status bar (`📜 Show Event Log ▲`) to inspect real-time Raspberry Pi bridge operations, powerline transactions, and session heartbeats alongside any active workspace. |

---

## 🚀 Quick Start & Deployment

### 1. Backend Service (Linux / Raspberry Pi)
```bash
# Clone the repository to the host
cd /opt
sudo git clone https://github.com/crcowan/Insteon-Manager.git insteon-bridge
cd /opt/insteon-bridge/backend

# Create virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure environment (.env)
cp .env.example .env
# Edit OPENHAB_URL, OPENHAB_API_TOKEN, and PLM_PORT

# Install and start systemd service
sudo cp ../deployment/insteon-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now insteon-bridge
```

### 2. Frontend Desktop Application (Windows)
```powershell
# Package self-contained single-file executable to dist\InsteonManager
powershell -ExecutionPolicy Bypass -File .\package_frontend.ps1

# Launch the packaged executable
.\dist\InsteonManager\InsteonManager.exe
```

---

## 🔒 Safety & Operational Guarantees
* **No Unverified Writes:** No database commit occurs without positive read-back acknowledgment from the device's physical micro-controller.
* **Protected Core PLM Links:** Group 0 & Group 1 links with the PLM modem (`53.C1.42`) are hardware-locked in the UI against accidental deletion.
* **Fail-Safe Restoration:** If a session disconnects unexpectedly, the watchdog guarantees that openHAB or your host automation system is automatically resumed.
* **Golden Snapshots:** Network configurations are automatically snapshotted before executing multi-device batch repairs.
