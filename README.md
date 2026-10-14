# Insteon Network Management GUI and API Bridge

A decoupled, two-part architecture for managing an Insteon home automation network, bypassing openHAB UI limitations while safeguarding the openHAB runtime engine.

---

## Architecture Overview

```
 +-------------------------------------------------------+
 |                 Windows Desktop PC                    |
 |                                                       |
 |   WPF / C# Visual Studio Solution (.NET)              |
 |   - Interactive Network Topology Map (Node Canvas)    |
 |   - ALDB Table Inspector                              |
 |   - Session Toggle Controller (Start / End Session)   |
 +---------------------------+---------------------------+
                             | REST (HTTP JSON)
                             | Heartbeat Watchdog Keep-Alive
                             v
 +-------------------------------------------------------+
 |                  Raspberry Pi 4                       |
 |                                                       |
 |  [ FastAPI Bridge Service ]                           |
 |    - Manages Session & Hardware Arbitration           |
 |    - Disables openHAB Thing -> Claims Serial Port     |
 |    - Wraps `pyinsteon` with Timeout & Retry Logic     |
 |    - Auto-Release Watchdog (Emergency Failsafe)       |
 |                                                       |
 |         +--------------------+                        |
 |         |  openHAB 5.1 REST  | (PUT /enable true/false)
 |         +--------------------+                        |
 |         |   Hardware PLM     | (/dev/ttyUSB0)         |
 +---------+--------------------+------------------------+
```

---

## Directory Structure

```
.
├── backend/                  # Python 3 FastAPI Backend (deployed on Pi 4)
│   ├── app/
│   │   ├── config.py         # App configuration & settings
│   │   ├── models.py         # Pydantic data schemas
│   │   ├── openhab.py        # openHAB REST client (Thing enable/disable)
│   │   ├── session.py        # Hardware arbitration, locks, watchdog lease
│   │   ├── insteon.py        # pyinsteon wrapper with powerline retry handling
│   │   └── main.py           # FastAPI REST endpoints
│   ├── requirements.txt      # Python dependencies
│   ├── .env.example          # Template environment configuration
│   └── README.md             # Pi installation instructions
├── frontend/                 # C# WPF Desktop GUI (Visual Studio)
│   ├── InsteonManager.sln    # Visual Studio Solution
│   └── InsteonManager/       # WPF Application Project
│       ├── Models/           # UI models (ALDB, DeviceNode, Session)
│       ├── Services/         # InsteonApiClient (REST client)
│       ├── ViewModels/       # MVVM ViewModel with auto-heartbeat & layout
│       └── MainWindow.xaml   # Canvas topology map & ALDB inspector
├── deployment/
│   └── insteon-bridge.service # systemd unit file for Raspberry Pi 4
└── README.md
```

---

## The Session Toggle Strategy

1. **Session-Level Lock (Preserves Port & OpenHAB Stability):**
   Rather than toggling openHAB off and on for each atomic request (which causes serial port thrashing and multi-second delays), the user initiates a **Management Session** in the UI.
2. **Deterministic Sequence:**
   - **Start Session**: FastAPI issues `PUT /rest/things/{thingUID}/enable` with `false` to openHAB. Once verified, FastAPI connects `pyinsteon` to `/dev/ttyUSB0`.
   - **Active Session**: High-speed ALDB queries, topology mapping, and configuration changes execute directly through `pyinsteon`.
   - **End Session**: FastAPI shuts down `pyinsteon`, releases the serial port, and issues `PUT /rest/things/{thingUID}/enable` with `true` to openHAB.
3. **Emergency Watchdog Failsafe:**
   The backend enforces a lease expiration timer (default 300 seconds). The frontend transmits a periodic background heartbeat every 30 seconds. If the frontend crashes, loses Wi-Fi, or is closed without ending the session, the backend automatically disconnects `pyinsteon` and re-enables openHAB!
