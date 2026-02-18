import asyncio
import math
import os
import threading
import time
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pymavlink import mavutil


class ManualControlRequest(BaseModel):
    x: int = Field(0, ge=-1000, le=1000)
    y: int = Field(0, ge=-1000, le=1000)
    z: int = Field(500, ge=0, le=1000)
    r: int = Field(0, ge=-1000, le=1000)
    buttons: int = 0


class MavlinkService:
    def __init__(self, connection_string: str) -> None:
        self.connection_string = connection_string
        self.master: Optional[mavutil.mavfile] = None
        self.connected = False
        self.latest: Dict[str, Dict[str, Any]] = {}
        self.last_seen_at: Optional[float] = None
        self._lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._stream_requested = False

    def start(self) -> None:
        self.master = mavutil.mavlink_connection(self.connection_string)
        self._stop_event.clear()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.0)

    def _reader_loop(self) -> None:
        watched = [
            "HEARTBEAT",
            "GLOBAL_POSITION_INT",
            "ATTITUDE",
            "SYS_STATUS",
            "GPS_RAW_INT",
            "HOME_POSITION",
        ]
        while not self._stop_event.is_set() and self.master is not None:
            if not self.connected:
                heartbeat = self.master.wait_heartbeat(timeout=1)
                if heartbeat is None:
                    time.sleep(0.1)
                    continue

                with self._lock:
                    self.connected = True
                    self.last_seen_at = time.time()
                self._request_streams()
                continue

            msg = self.master.recv_match(type=watched, blocking=False)
            if msg is None:
                time.sleep(0.02)
                continue

            payload = msg.to_dict()
            msg_type = payload.get("mavpackettype", "UNKNOWN")
            with self._lock:
                self.latest[msg_type] = payload
                self.last_seen_at = time.time()

    def _request_streams(self) -> None:
        if self.master is None or self._stream_requested:
            return

        try:
            self.master.mav.request_data_stream_send(
                self.master.target_system,
                self.master.target_component,
                mavutil.mavlink.MAV_DATA_STREAM_ALL,
                5,
                1,
            )
        except Exception:
            pass

        try:
            self.master.mav.command_long_send(
                self.master.target_system,
                self.master.target_component,
                mavutil.mavlink.MAV_CMD_GET_HOME_POSITION,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
            )
        except Exception:
            pass

        self._stream_requested = True

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            pos = self.latest.get("GLOBAL_POSITION_INT", {})
            att = self.latest.get("ATTITUDE", {})
            gps = self.latest.get("GPS_RAW_INT", {})
            hb = self.latest.get("HEARTBEAT", {})
            sys_status = self.latest.get("SYS_STATUS", {})
            home = self.latest.get("HOME_POSITION", {})

            lat = pos.get("lat")
            lon = pos.get("lon")
            rel_alt = pos.get("relative_alt")
            home_lat = home.get("latitude")
            home_lon = home.get("longitude")
            home_alt = home.get("altitude")

            snapshot = {
                "connected": self.connected,
                "last_seen_at": self.last_seen_at,
                "target_system": self.master.target_system if self.master else 0,
                "target_component": self.master.target_component if self.master else 0,
                "position": {
                    "lat_deg": lat / 1e7 if lat is not None else None,
                    "lon_deg": lon / 1e7 if lon is not None else None,
                    "relative_alt_m": rel_alt / 1000.0 if rel_alt is not None else None,
                },
                "home": {
                    "lat_deg": home_lat / 1e7 if home_lat is not None else None,
                    "lon_deg": home_lon / 1e7 if home_lon is not None else None,
                    "alt_m": home_alt / 1000.0 if home_alt is not None else None,
                },
                "attitude": {
                    "roll_rad": att.get("roll"),
                    "pitch_rad": att.get("pitch"),
                    "yaw_rad": att.get("yaw"),
                },
                "gps": {
                    "fix_type": gps.get("fix_type"),
                    "satellites_visible": gps.get("satellites_visible"),
                },
                "battery": {
                    "voltage_v": (sys_status.get("voltage_battery") or 0) / 1000.0
                    if sys_status.get("voltage_battery") is not None
                    else None,
                    "remaining_pct": sys_status.get("battery_remaining"),
                },
                "raw": {
                    "HEARTBEAT": hb,
                    "GLOBAL_POSITION_INT": pos,
                    "ATTITUDE": att,
                    "GPS_RAW_INT": gps,
                    "SYS_STATUS": sys_status,
                    "HOME_POSITION": home,
                },
            }
            return _sanitize_for_json(snapshot)

    def send_manual_control(self, req: ManualControlRequest) -> None:
        if self.master is None:
            raise RuntimeError("MAVLink connection is not initialized")
        if self.master.target_system == 0:
            raise RuntimeError("Target system is unknown. Wait for heartbeat first")

        self.master.mav.manual_control_send(
            self.master.target_system,
            req.x,
            req.y,
            req.z,
            req.r,
            req.buttons,
        )


app = FastAPI(title="Rover GCS Backend", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

mav = MavlinkService(
    connection_string=os.getenv("MAVLINK_CONNECTION", "udpin:127.0.0.1:14550")
)


def _sanitize_for_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_for_json(v) for v in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


@app.on_event("startup")
async def startup_event() -> None:
    mav.start()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    mav.stop()


@app.get("/api/health")
async def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "connected": mav.connected,
        "target_system": mav.master.target_system if mav.master else None,
        "target_component": mav.master.target_component if mav.master else None,
    }


@app.get("/api/telemetry")
async def telemetry() -> Dict[str, Any]:
    return mav.snapshot()


@app.post("/api/manual-control")
async def manual_control(req: ManualControlRequest) -> Dict[str, Any]:
    try:
        mav.send_manual_control(req)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.websocket("/ws/telemetry")
async def ws_telemetry(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(mav.snapshot())
            await asyncio.sleep(0.2)
    except WebSocketDisconnect:
        return
