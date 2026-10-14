# Insteon Bridge (FastAPI Backend)

FastAPI bridge service running on Raspberry Pi 4 to arbitrate hardware access between openHAB and `pyinsteon`, providing a high-performance REST API for network management.

## Setup on Raspberry Pi 4

1. Clone or copy the `backend` folder to the Pi (e.g., `/opt/insteon-bridge`).
2. Create a virtual environment and install dependencies:
   ```bash
   cd /opt/insteon-bridge
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and configure:
   ```bash
   cp .env.example .env
   nano .env
   ```
   Set:
   - `OPENHAB_URL`: e.g. `http://localhost:8080`
   - `OPENHAB_TOKEN`: API token generated in openHAB
   - `OPENHAB_THING_UID`: e.g. `insteon:network:local`
   - `INSTEON_PORT`: USB PLM port (e.g. `/dev/ttyUSB0` or `/dev/serial/by-id/...`)
4. Test running locally:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
5. View API documentation at `http://<pi-ip>:8000/docs`.
