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
* **Hardware-Enforced Protection:** The suite strictly guards these core communication links. The UI blocks their deletion, the Health Analyzer suppresses false duplicate warnings on them, and automated batch cleanups will never sever them.

### 3. Atomic Two-Phase Commits (2PC) & Physical Silicon Verification
Powerline and RF dual-mesh networks are subject to noise, attenuation, and packet collisions. Optimistic databases frequently drift from physical reality.
* **2-Phase Verification:** Every link creation, preset adjustment, or record deletion writes to device EEPROM and immediately executes a physical read-back (`_clean_peek` with powerline echo dissipation).
* **Automatic Rollback:** If the responder fails verification (e.g., powerline echo corruption), the controller link is immediately rolled back, preventing half-links and orphaned records.
* **Zero Database Fantasy:** The database snapshot is only updated once the physical silicon confirms the byte sequence.

### 4. High-Performance Network Maintenance & Surgical EEPROM Tuning
* **Surgical Preset Adjustment:** Adjusting a responder's On-Level (brightness) or Ramp Rate (fade speed) modifies *only* that responder's specific 8-byte EEPROM record. Other scene members and controllers remain untouched, eliminating the risk and multi-minute traffic of full scene rebuilds.
* **Multi-Cast Scene Optimization:** Multicast All-Link commands are leveraged for simultaneous multi-device firing, eliminating the "popcorn effect" of sequential commands.
* **Device Replacement Wizard ("Clone & Swap"):** Replacing a broken switch historically required manually rebuilding dozens of scenes. The Clone & Swap engine reads the legacy device's records, writes reciprocal links to the replacement switch, and systematically retargets every peer switch and keypad in the house to point to the new address in a single coordinated operation.

### 5. Heuristic Network Health Analyzer (7-Step Diagnostics)
A comprehensive diagnostic engine inspects the entire network topology against an authoritative rule-base:
1. **Ghost Targets:** Eradicates dangling link records pointing to retired/unplugged hardware (eliminating wasted retransmit retries that degrade network responsiveness).
2. **Missing Core Links:** Detects unlinked devices missing critical Group 0 (management) or Group 1 (status reporting) links with the PLM.
3. **Loopbacks:** Identifies corrupted records where a device targets its own address.
4. **Duplicate EEPROM Slots:** Detects redundant identical link records consuming limited EEPROM space.
5. **Half-Links:** Surfaces one-way links where a controller has no reciprocal responder (or vice-versa), offering 1-click reciprocal repair.
6. **Corrupted Group 0 Records:** Cleans invalid Group 0 records on non-PLM devices.
7. **Scene Health:** Flags obsolete diagnostic artifacts (e.g., Group 251 engine pings), empty zombie scenes (controllers with 0 responders), and single-device redundant scenes with 1-click purge actions.

---

## 🏗️ System Architecture

```
 +-------------------------------------------------------------------------+
 |                           Windows Desktop                               |
 |                                                                         |
 |   Insteon Manager (.NET 10 WPF / MVVM)                                  |
 |   ├── Interactive Network Topology Canvas (Zoom, Pan, State Visualizer) |
 |   ├── Deep Device ALDB Inspector & Live Bitmask Decoder                 |
 |   ├── Staged Changes & Diff Review Engine                               |
 |   ├── Insteon Scenes & Live Preset Tuner (Brightness & Ramp Sliders)    |
 |   ├── Device Replacement Wizard ("Clone & Swap")                        |
 |   └── Network Health & Topology Analyzer (7-Step Diagnostics)           |
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

## 🖥️ Application Features & User Interface

| Tab | Feature | Description |
|---|---|---|
| **Tab 1** | **🕸️ Network Topology Map** | Interactive force-directed node graph showing all devices, link reciprocity, signal paths, and real-time device control (On/Off/Dim). |
| **Tab 2** | **📑 Device ALDB Inspector** | Deep inspection of raw EEPROM records. Categorizes Controller vs. Responder links, decodes high/low bitmasks, and provides direct hardware querying. |
| **Tab 3** | **📋 Staged Changes & Commit** | Safety workbench allowing complex linking, unlinking, and scene changes to be reviewed as diffs before touching physical hardware. |
| **Tab 4** | **🎭 Insteon Scenes & Presets** | Create new scenes, delete obsolete scenes, add responder devices with custom ramp rates, and adjust brightness presets surgically with instant hardware verification. |
| **Tab 5** | **⚡ Advanced Operations & Recovery** | Atomic reciprocal link creation/deletion, ghost target purge, and the seamless "Clone & Swap" Device Replacement Wizard. |
| **Tab 6** | **🩺 Network Health Analyzer** | 7-step automated health inspection scoring network integrity from 0–100% with 1-click batch auto-repair for half-links and ghost targets. |

---

## 🚀 Quick Start & Deployment

### 1. Backend Service (Linux / Raspberry Pi)
```bash
# Clone the repository to the host
cd /opt
sudo git clone https://github.com/your-repo/insteon-manager.git insteon-bridge
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
# Open solution in Visual Studio or build via .NET CLI
cd "frontend\InsteonManager"
dotnet publish -c Release -o "..\..\dist\InsteonManager"

# Launch the packaged executable
.\dist\InsteonManager\InsteonManager.exe
```

---

## 🔒 Safety & Operational Guarantees
* **No Unverified Writes:** No database commit occurs without positive read-back acknowledgment from the device's physical micro-controller.
* **Fail-Safe Restoration:** If a session disconnects unexpectedly, the watchdog guarantees that openHAB or your host automation system is automatically resumed.
* **Golden Snapshots:** Network configurations are automatically snapshotted before executing multi-device batch repairs.
