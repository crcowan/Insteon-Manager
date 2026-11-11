# Insteon Network Studio: Topology, Diagnostics & ALDB Management Suite

An enterprise-grade, decoupled desktop and bridge management suite for Insteon dual-mesh (Powerline + RF) smart home networks. Designed to bypass the operational and diagnostic limitations of generic smart-home platforms while guaranteeing physical EEPROM integrity, high network performance, and rock-solid system stability.

---

## 📑 Table of Contents
1. [📦 Download & Installation Guide (No Compiling Required)](#-download--installation-guide-no-compiling-required)
   - [Prerequisites](#-prerequisites)
   - [Part 1: Install the Raspberry Pi Bridge (`InsteonBridge-RaspberryPi`)](#part-1-install-the-raspberry-pi-bridge-insteonbridge-raspberrypi)
   - [Part 2: Install the Windows PC Studio (`InsteonManager-Windows-x64`)](#part-2-install-the-windows-pc-studio-insteonmanager-windows-x64)
   - [Part 3: First-Run Connection & Network Discovery](#part-3-first-run-connection--network-discovery)
   - [Upgrading an Existing Installation](#-upgrading-an-existing-installation)
2. [🖥️ Studio Workspaces & User Interface Architecture](#️-studio-workspaces--user-interface-architecture)
3. [🏛️ Key Architectural Decisions & Engineering Principles](#️-key-architectural-decisions--engineering-principles)
4. [🏗️ System Architecture](#️-system-architecture)
5. [🛠️ Building & Packaging from Source (For Developers)](#️-building--packaging-from-source-for-developers)
6. [🔒 Safety & Operational Guarantees](#-safety--operational-guarantees)

---

## 📦 Download & Installation Guide (No Compiling Required)

Pre-built, ready-to-run release packages for both the **Windows 10/11 Desktop Studio** and the **Raspberry Pi Bridge Service** are available directly on the [**GitHub Releases**](https://github.com/crcowan/Insteon-Manager/releases/latest) page. **You do not need to install compilers, Visual Studio, or the .NET SDK/Runtime to use this application.**

### ✅ Prerequisites
* **Raspberry Pi / Linux Host:** Raspberry Pi 3, 4, or 5 (running Raspberry Pi OS, openHABian, Debian, or Ubuntu) with a physical **Insteon PLM Modem** (`2413U` USB, `2413S` Serial, or `2448A7` USB Stick) connected.
* **Windows PC:** Windows 10 or Windows 11 (64-bit / `x64`) connected to the same local network (LAN) as your Raspberry Pi.

---

### Part 1: Install the Raspberry Pi Bridge (`InsteonBridge-RaspberryPi`)

The Raspberry Pi Bridge runs as a lightweight background `systemd` service (`/opt/insteon-bridge`) on port `8000`. It arbitrates hardware access to the USB PLM modem with openHAB (or runs standalone) and executes verified powerline/RF transactions.

Choose **any one** of the three installation methods below:

#### Option A: 1-Line Turnkey Install Directly on the Raspberry Pi *(Recommended)*
Open an SSH session or terminal on your Raspberry Pi and run:
```bash
curl -sSL https://github.com/crcowan/Insteon-Manager/releases/latest/download/InsteonBridge-RaspberryPi.tar.gz | tar -xz && cd InsteonBridge-RaspberryPi && sudo bash install_pi.sh
```
The installer will automatically:
1. Ensure Python 3 `venv` and `pip` are installed on your Raspberry Pi.
2. Install the FastAPI bridge application into `/opt/insteon-bridge` and create an isolated Python virtual environment (`.venv`).
3. Prompt you for your openHAB URL, API Token, Insteon Thing UID, and USB PLM serial port (or preserve your existing `/opt/insteon-bridge/.env` if upgrading).
4. Grant serial port permissions (`dialout` group) to your Linux user.
5. Register, enable, and start the `insteon-bridge.service` background daemon via `systemd`.

#### Option B: 1-Click Remote Push Installer from Your Windows PC *(No Linux Terminal Needed)*
If you prefer to push the installation directly from your Windows PC over SSH:
1. Download [**`InsteonBridge-RaspberryPi.zip`**](https://github.com/crcowan/Insteon-Manager/releases/latest/download/InsteonBridge-RaspberryPi.zip) from the [Latest GitHub Release](https://github.com/crcowan/Insteon-Manager/releases/latest) and extract it on your Windows PC.
2. Double-click **`Install-PiBridge-From-PC.cmd`** inside the extracted folder.
3. Follow the on-screen prompts to enter your Raspberry Pi hostname/IP (default: `openhabian5`), SSH username (default: `openhabian`), and optional openHAB settings.
4. The script will upload all bridge files via `scp` and run the automated `systemd` service installer on your Raspberry Pi.

#### Option C: Manual Archive Download & Install on the Raspberry Pi
1. Download [**`InsteonBridge-RaspberryPi.tar.gz`**](https://github.com/crcowan/Insteon-Manager/releases/latest/download/InsteonBridge-RaspberryPi.tar.gz) from the [Latest GitHub Release](https://github.com/crcowan/Insteon-Manager/releases/latest) onto your Raspberry Pi.
2. Extract and run the installer:
   ```bash
   tar -xzf InsteonBridge-RaspberryPi.tar.gz
   cd InsteonBridge-RaspberryPi
   sudo bash install_pi.sh
   ```

#### ⚙️ Configuring `/opt/insteon-bridge/.env` on the Raspberry Pi
If you skipped the interactive prompts during installation or need to update your settings later, edit `/opt/insteon-bridge/.env`:
```bash
sudo nano /opt/insteon-bridge/.env
```
Key settings in `/opt/insteon-bridge/.env`:
| Variable | Default / Example | Description |
|---|---|---|
| `OPENHAB_URL` | `http://localhost:8080` | URL of your local openHAB server. |
| `OPENHAB_TOKEN` | `oh.insteonbridge.xxxx` | Generate in openHAB UI: click your **Profile (bottom-left)** → **Create new API Token**. |
| `OPENHAB_THING_UID` | `insteon:network:local` | Your openHAB Insteon Bridge Thing UID (found in openHAB UI under **Settings → Things**). |
| `INSTEON_PORT` | `/dev/ttyUSB0` | Serial port of your USB PLM (tip: check `ls -l /dev/serial/by-id/` for a persistent path). |
| `PORT` | `8000` | Port exposed by the Insteon Bridge HTTP API. |

After editing `/opt/insteon-bridge/.env`, restart the service:
```bash
sudo systemctl restart insteon-bridge
sudo systemctl status insteon-bridge
```

---

### Part 2: Install the Windows PC Studio (`InsteonManager-Windows-x64`)

The Windows Desktop application is packaged as a **100% self-contained single-file executable (`InsteonManager.exe`)**. All required .NET 10 and WPF native libraries are bundled directly inside the executable—nothing else needs to be installed.

1. **Download the Windows Release ZIP:**
   Download [**`InsteonManager-Windows-x64.zip`**](https://github.com/crcowan/Insteon-Manager/releases/latest/download/InsteonManager-Windows-x64.zip) from the [Latest GitHub Release](https://github.com/crcowan/Insteon-Manager/releases/latest).
2. **Extract to a Permanent Folder:**
   Right-click `InsteonManager-Windows-x64.zip` → **Extract All...** and extract it to a permanent folder on your PC (for example, `C:\InsteonManager` or `C:\Users\<YourName>\AppData\Local\InsteonManager`).
   > **Tip:** Before extracting, you can right-click the downloaded `.zip` file → **Properties** → check **Unblock** (at the bottom) → **Apply** so Windows does not show a SmartScreen prompt on first launch.
3. **Create the Desktop Shortcut (1 Click):**
   Open the extracted folder and double-click **`Install-DesktopShortcut.cmd`**.
   This automatically places an **Insteon Manager** shortcut (with the custom studio icon) directly onto your Windows Desktop.
4. **Launch Insteon Network Studio:**
   Double-click the **Insteon Manager** icon on your Desktop (or run `InsteonManager.exe` directly from the folder).

---

### Part 3: First-Run Connection & Network Discovery

1. **Connect to Your Raspberry Pi Bridge:**
   * Look at the top-right corner of the **Insteon Network Studio** window.
   * In the **`Bridge:`** text box, enter the IP address or hostname of your Raspberry Pi on port `8000`:
     ```text
     http://openhabian5:8000
     ```
     *(or e.g. `http://192.168.1.50:8000` — the exact URL is printed at the end of `install_pi.sh`)*.
   * Click **`Test Link`**. The status bar will confirm connectivity and load your cached network snapshot in **`👁️ BROWSE MODE (Read-Only Cache)`** (leaving openHAB `ONLINE` and uninterrupted).
2. **Initial Hardware Crawl (First-Time Setup Only):**
   * If you are setting up the bridge for the very first time and have not yet scanned your Insteon network:
     1. Click **`🛠️ Enter Manage Mode`** in the top command bar. The bridge will safely pause your openHAB Insteon Thing and lock `/dev/ttyUSB0`.
     2. Navigate to the **`🔌 Devices & Links`** workspace and click **`🔄 Crawl Hardware`**.
     3. Once the hardware scan finishes, click **`👁️ Return to Browse Mode`** to immediately hand the USB PLM back to openHAB!

---

### 🔄 Upgrading an Existing Installation

* **Upgrading the Windows PC Studio:**
  Close `InsteonManager.exe`, download the latest [`InsteonManager-Windows-x64.zip`](https://github.com/crcowan/Insteon-Manager/releases/latest/download/InsteonManager-Windows-x64.zip), and extract/overwrite the files in your installation folder. Your layout and UI settings (`node_positions.json` and `ui_settings.json`) are preserved automatically.
* **Upgrading the Raspberry Pi Bridge:**
  Re-run the 1-line installer on the Raspberry Pi (or run `Install-PiBridge-From-PC.cmd` on Windows). The installer preserves your existing `/opt/insteon-bridge/.env` and cached `network_snapshot.json` while updating the Python application and restarting `insteon-bridge.service`.

---

## 🖥️ Studio Workspaces & User Interface Architecture

**Insteon Network Studio** separates **Browse Mode** (`👁️ BROWSE MODE (Read-Only Cache)` — full exploration and change staging while openHAB stays `ONLINE`) from **Manage Mode** (`🛠️ MANAGE MODE (Live Hardware Exclusive)` — exclusive PLM hardware lock for live powerline commands and verified EEPROM commits, with automatic escalation prompts when needed).

### 4 Primary Workspaces
| Workspace | Sub-Views & Capabilities | Description |
|---|---|---|
| **1. `🌐 Network Overview`** | **Interactive Topology Map & Node Inspector Sidebar** | Node-and-link canvas with directional link encoding (**Solid Blue** `Controls`, **Dotted Green** `Responds To`, **Dashed Gold** `Cross-Linked`, vector Arrow Tail/Head markers, and dynamic selected-node perspective), hardware identity ribbons, and 1-click navigation to Device Inspector or Link Tools. |
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

## 🏛️ Key Architectural Decisions & Engineering Principles

### 1. Host-Agnostic Hardware Arbitration (openHAB & Beyond)
Generic smart home hubs (openHAB, Home Assistant, etc.) lock serial interfaces continuously, making concurrent deep diagnostics impossible without port collision.
* **Management Session Lease:** Rather than thrashing the serial port per command, Insteon Manager uses a stateful **Management Session**. Upon acquisition, the bridge safely pauses host bindings via REST (e.g. openHAB Insteon Thing `PUT /rest/things/{uid}/enable false`), transfers ownership of the USB PLM (`/dev/ttyUSB0`) to `pyinsteon`, and begins high-speed management.
* **Watchdog Keep-Alive:** The desktop client issues periodic heartbeats (30s interval). If the client disconnects or crashes, an autonomous backend watchdog releases the serial port and restores the host automation engine to `ONLINE` within 300 seconds.
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
 |   Insteon Network Studio (.NET 10 WPF / MVVM — Self-Contained .exe)     |
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

## 🛠️ Building & Packaging from Source (For Developers)

If you are modifying the source code and want to build the self-contained Windows executable or generate new release archives yourself (requires the [.NET 10 SDK](https://dotnet.microsoft.com/download)):

```powershell
# 1. Build & package the self-contained single-file Windows app into dist\InsteonManager
powershell -NoProfile -ExecutionPolicy Bypass -File .\package_frontend.ps1

# 2. Build all GitHub Release archives (Windows .zip + Raspberry Pi .tar.gz / .zip) in dist\releases\v2.0.0
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_release.ps1 -Version "v2.0.0"

# 3. Deploy updated Python backend code directly to a Raspberry Pi over SSH
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy_backend_code.ps1
```

---

## 🔒 Safety & Operational Guarantees
* **No Unverified Writes:** No database commit occurs without positive read-back acknowledgment from the device's physical micro-controller.
* **Protected Core PLM Links:** Group 0 & Group 1 links with the PLM modem (`53.C1.42`) are hardware-locked in the UI against accidental deletion.
* **Fail-Safe Restoration:** If a session disconnects unexpectedly, the watchdog guarantees that openHAB or your host automation system is automatically resumed.
* **Golden Snapshots:** Network configurations are automatically snapshotted before executing multi-device batch repairs.
