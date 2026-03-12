import asyncio
import json
import logging
import math
import os
import shutil
import subprocess
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pymavlink import mavutil

logger = logging.getLogger(__name__)


class ManualControlRequest(BaseModel):
    x: int = Field(0, ge=-1000, le=1000)
    y: int = Field(0, ge=-1000, le=1000)
    z: int = Field(500, ge=0, le=1000)
    r: int = Field(0, ge=-1000, le=1000)
    buttons: int = 0


class NetworkPolicyRequest(BaseModel):
    wifi_ssid: str = Field(..., min_length=1, max_length=64)
    tethering_ssid: str = Field(..., min_length=1, max_length=64)


class NetworkConnectRequest(BaseModel):
    role: Literal["wifi", "tethering"]
    ssid: str = Field(..., min_length=1, max_length=64)
    password: str = Field("", max_length=128)
    persist_to_policy: bool = True


class NetworkManagerService:
    def __init__(self, interface: str, policy_path: Path, monitor_interval_sec: int) -> None:
        self.interface = interface
        self.policy_path = policy_path
        self.monitor_interval_sec = max(5, monitor_interval_sec)
        self._lock = threading.Lock()
        self._last_apply: Dict[str, Any] = {
            "status": "idle",
            "message": "not started",
            "target_role": None,
            "at": None,
        }
        try:
            self._ensure_policy_file()
        except Exception as exc:
            self._set_last_apply("error", f"policy init failed: {exc}", None)

    @property
    def nmcli_available(self) -> bool:
        return shutil.which("nmcli") is not None

    def _default_policy(self) -> Dict[str, Any]:
        return {
            "wifi_ssid": "",
            "tethering_ssid": "",
            "updated_at": None,
        }

    def _ensure_policy_file(self) -> None:
        self.policy_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.policy_path.exists():
            self.policy_path.write_text(
                json.dumps(self._default_policy(), ensure_ascii=True, indent=2),
                encoding="utf-8",
            )

    def _load_policy_unlocked(self) -> Dict[str, Any]:
        try:
            raw = self.policy_path.read_text(encoding="utf-8").strip()
            if not raw:
                return self._default_policy()
            parsed = json.loads(raw)
        except Exception:
            return self._default_policy()

        policy = self._default_policy()
        if isinstance(parsed, dict):
            policy["wifi_ssid"] = str(parsed.get("wifi_ssid") or "").strip()
            policy["tethering_ssid"] = str(parsed.get("tethering_ssid") or "").strip()
            policy["updated_at"] = parsed.get("updated_at")
        return policy

    def _save_policy_unlocked(self, policy: Dict[str, Any]) -> None:
        self.policy_path.write_text(
            json.dumps(policy, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

    def get_policy(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._load_policy_unlocked())

    def set_policy(self, wifi_ssid: str, tethering_ssid: str) -> Dict[str, Any]:
        with self._lock:
            policy = self._load_policy_unlocked()
            policy["wifi_ssid"] = wifi_ssid.strip()
            policy["tethering_ssid"] = tethering_ssid.strip()
            policy["updated_at"] = time.time()
            self._save_policy_unlocked(policy)
        self._apply_autoconnect_priority(policy)
        return policy

    def _require_nmcli(self) -> None:
        if not self.nmcli_available:
            raise RuntimeError("nmcli not found. Install NetworkManager on Raspberry Pi")

    def _run_nmcli(self, args: list[str], timeout_sec: int = 20) -> str:
        self._require_nmcli()
        try:
            proc = subprocess.run(
                ["nmcli", *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"nmcli timeout: {' '.join(args)}") from exc

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        if proc.returncode != 0:
            msg = stderr or stdout or f"nmcli exited with status {proc.returncode}"
            raise RuntimeError(msg)
        return stdout

    @staticmethod
    def _split_nmcli_row(line: str, fields: int) -> list[str]:
        chunks: list[str] = []
        buf: list[str] = []
        escaped = False

        for ch in line:
            if escaped:
                buf.append(ch)
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == ":" and len(chunks) < fields - 1:
                chunks.append("".join(buf))
                buf = []
                continue
            buf.append(ch)

        chunks.append("".join(buf))
        while len(chunks) < fields:
            chunks.append("")
        return chunks

    def _set_last_apply(self, status: str, message: str, target_role: Optional[str]) -> None:
        with self._lock:
            self._last_apply = {
                "status": status,
                "message": message,
                "target_role": target_role,
                "at": time.time(),
            }

    def get_last_apply(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._last_apply)

    def scan_wifi(self, rescan: bool = True) -> list[Dict[str, Any]]:
        args = [
            "-t",
            "-f",
            "IN-USE,SSID,SIGNAL,SECURITY",
            "device",
            "wifi",
            "list",
            "ifname",
            self.interface,
        ]
        if rescan:
            args.extend(["--rescan", "yes"])

        output = self._run_nmcli(args)
        by_ssid: Dict[str, Dict[str, Any]] = {}
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            in_use, ssid, signal, security = self._split_nmcli_row(line, 4)
            ssid = ssid.strip()
            if not ssid:
                continue
            try:
                signal_val: Optional[int] = int(signal)
            except ValueError:
                signal_val = None

            current = {
                "ssid": ssid,
                "signal": signal_val,
                "security": security.strip(),
                "in_use": in_use.strip() == "*",
            }
            existing = by_ssid.get(ssid)
            if existing is None:
                by_ssid[ssid] = current
                continue
            old_signal = existing.get("signal")
            if old_signal is None or (signal_val is not None and signal_val > old_signal):
                by_ssid[ssid] = current

        return sorted(
            by_ssid.values(),
            key=lambda item: (
                1 if item.get("in_use") else 0,
                item.get("signal") if item.get("signal") is not None else -1,
                item.get("ssid") or "",
            ),
            reverse=True,
        )

    def get_active_connection(self) -> Dict[str, Any]:
        output = self._run_nmcli(["-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"])
        active = {
            "name": None,
            "ssid": None,
            "type": None,
            "device": None,
        }
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            name, conn_type, device = self._split_nmcli_row(line, 3)
            if conn_type == "802-11-wireless" and device == self.interface:
                active = {
                    "name": name,
                    "ssid": None,
                    "type": conn_type,
                    "device": device,
                }
                break
        if active.get("name"):
            with suppress(Exception):
                for entry in self.scan_wifi(rescan=False):
                    if entry.get("in_use"):
                        active["ssid"] = entry.get("ssid")
                        break
            if not active.get("ssid"):
                active["ssid"] = active.get("name")
        return active

    def _find_connection_name_by_ssid(self, ssid: str) -> Optional[str]:
        target = ssid.strip()
        if not target:
            return None
        output = self._run_nmcli(["-t", "-f", "NAME,TYPE,802-11-wireless.ssid", "connection", "show"])
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            name, conn_type, conn_ssid = self._split_nmcli_row(line, 3)
            name = name.strip()
            conn_ssid = conn_ssid.strip()
            if conn_type != "802-11-wireless":
                continue
            if conn_ssid == target or name == target:
                return name
        return None

    def _connect_to_ssid(self, ssid: str, password: str = "") -> None:
        target = ssid.strip()
        if not target:
            raise RuntimeError("SSID is required")

        errors: list[str] = []
        profile_name: Optional[str] = None
        with suppress(Exception):
            profile_name = self._find_connection_name_by_ssid(target)

        if profile_name:
            try:
                self._run_nmcli(["--wait", "20", "connection", "up", "id", profile_name, "ifname", self.interface])
                return
            except Exception as exc:
                errors.append(f"profile up ({profile_name}): {exc}")

        args = ["--wait", "20", "device", "wifi", "connect", target, "ifname", self.interface]
        if password:
            args.extend(["password", password])
        try:
            self._run_nmcli(args)
            return
        except Exception as exc:
            errors.append(f"wifi connect ({target}): {exc}")

        try:
            self._run_nmcli(["--wait", "20", "connection", "up", "id", target, "ifname", self.interface])
            return
        except Exception as exc:
            errors.append(f"profile up ({target}): {exc}")

        raise RuntimeError("; ".join(errors))

    def _set_connection_priority(self, ssid: str, priority: int) -> None:
        if not ssid:
            return
        conn_name = ssid
        with suppress(Exception):
            found = self._find_connection_name_by_ssid(ssid)
            if found:
                conn_name = found
        with suppress(Exception):
            self._run_nmcli(
                [
                    "connection",
                    "modify",
                    conn_name,
                    "connection.autoconnect",
                    "yes",
                    "connection.autoconnect-priority",
                    str(priority),
                ]
            )

    def _apply_autoconnect_priority(self, policy: Dict[str, Any]) -> None:
        wifi_ssid = str(policy.get("wifi_ssid") or "").strip()
        tether_ssid = str(policy.get("tethering_ssid") or "").strip()
        self._set_connection_priority(wifi_ssid, 100)
        self._set_connection_priority(tether_ssid, 50)

    def connect(
        self,
        role: Literal["wifi", "tethering"],
        ssid: str,
        password: str,
        persist_to_policy: bool = True,
    ) -> Dict[str, Any]:
        target_ssid = ssid.strip()
        if not target_ssid:
            raise RuntimeError("SSID is required")

        self._connect_to_ssid(target_ssid, password)

        if persist_to_policy:
            with self._lock:
                policy = self._load_policy_unlocked()
                key = "wifi_ssid" if role == "wifi" else "tethering_ssid"
                policy[key] = target_ssid
                policy["updated_at"] = time.time()
                self._save_policy_unlocked(policy)
            self._apply_autoconnect_priority(policy)

        active = self.get_active_connection()
        self._set_last_apply("connected", f"connected to {target_ssid}", role)
        return {
            "role": role,
            "ssid": target_ssid,
            "active": active,
            "policy": self.get_policy(),
        }

    def apply_priority_policy(self) -> Dict[str, Any]:
        policy = self.get_policy()
        wifi_ssid = str(policy.get("wifi_ssid") or "").strip()
        tether_ssid = str(policy.get("tethering_ssid") or "").strip()

        if not wifi_ssid and not tether_ssid:
            result = {
                "ok": False,
                "status": "missing-policy",
                "message": "set wifi/tethering SSID first",
                "policy": policy,
            }
            self._set_last_apply(result["status"], result["message"], None)
            return result

        visible = {entry["ssid"] for entry in self.scan_wifi(rescan=False)}
        target_role: Optional[Literal["wifi", "tethering"]] = None
        target_ssid = ""
        if wifi_ssid and wifi_ssid in visible:
            target_role = "wifi"
            target_ssid = wifi_ssid
        elif tether_ssid and tether_ssid in visible:
            target_role = "tethering"
            target_ssid = tether_ssid
        else:
            result = {
                "ok": False,
                "status": "not-visible",
                "message": "no configured SSID is visible right now",
                "policy": policy,
                "visible_ssids": sorted(visible),
            }
            self._set_last_apply(result["status"], result["message"], None)
            return result

        active = self.get_active_connection()
        if active.get("ssid") == target_ssid:
            result = {
                "ok": True,
                "status": "already-optimal",
                "target_role": target_role,
                "target_ssid": target_ssid,
                "active": active,
                "policy": policy,
            }
            self._set_last_apply(result["status"], f"already on {target_ssid}", target_role)
            return result

        self._connect_to_ssid(target_ssid)
        active = self.get_active_connection()
        result = {
            "ok": True,
            "status": "switched",
            "target_role": target_role,
            "target_ssid": target_ssid,
            "active": active,
            "policy": policy,
        }
        self._set_last_apply(result["status"], f"switched to {target_ssid}", target_role)
        return result

    def status(self) -> Dict[str, Any]:
        policy = self.get_policy()
        last_apply = self.get_last_apply()

        if not self.nmcli_available:
            return {
                "ok": False,
                "nmcli_available": False,
                "interface": self.interface,
                "policy": policy,
                "last_apply": last_apply,
                "active": {"name": None, "ssid": None, "type": None, "device": None},
                "error": "nmcli not found",
            }

        try:
            active = self.get_active_connection()
            visible = self.scan_wifi(rescan=False)
            return {
                "ok": True,
                "nmcli_available": True,
                "interface": self.interface,
                "policy": policy,
                "last_apply": last_apply,
                "active": active,
                "visible_networks": visible,
            }
        except Exception as exc:
            return {
                "ok": False,
                "nmcli_available": True,
                "interface": self.interface,
                "policy": policy,
                "last_apply": last_apply,
                "active": {"name": None, "ssid": None, "type": None, "device": None},
                "error": str(exc),
            }


class MavlinkService:
    def __init__(self, connection_string: str) -> None:
        self.connection_string = connection_string
        self.master: Optional[mavutil.mavfile] = None
        self.connected = False
        self.start_error: Optional[str] = None
        self.latest: Dict[str, Dict[str, Any]] = {}
        self.last_seen_at: Optional[float] = None
        self._lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._stream_requested = False

    def start(self) -> None:
        try:
            self.master = mavutil.mavlink_connection(self.connection_string)
        except Exception as exc:
            self.master = None
            self.connected = False
            self.start_error = f"mavlink init failed: {exc}"
            return
        self.start_error = None
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


class VideoHub:
    def __init__(self) -> None:
        self.latest_frame: Optional[bytes] = None
        self._viewers: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def add_viewer(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._viewers.add(websocket)
            frame = self.latest_frame
        if frame:
            await websocket.send_bytes(frame)

    async def remove_viewer(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._viewers.discard(websocket)

    async def publish(self, frame: bytes) -> None:
        async with self._lock:
            self.latest_frame = frame
            viewers = list(self._viewers)

        dead: list[WebSocket] = []
        for viewer in viewers:
            try:
                await viewer.send_bytes(frame)
            except Exception:
                dead.append(viewer)

        if dead:
            async with self._lock:
                for viewer in dead:
                    self._viewers.discard(viewer)


app = FastAPI(title="Rover GCS Backend", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


mav = MavlinkService(
    connection_string=os.getenv("MAVLINK_CONNECTION", "udpin:127.0.0.1:14550")
)
video_hub = VideoHub()
network_manager = NetworkManagerService(
    interface=os.getenv("WIFI_INTERFACE", "wlan0"),
    policy_path=Path(
        os.getenv(
            "NETWORK_POLICY_PATH",
            str(Path.home() / ".config" / "rover-gcs" / "network_policy.json"),
        )
    ),
    monitor_interval_sec=_env_int("NETWORK_POLICY_INTERVAL_SEC", 20),
)
network_policy_task: Optional[asyncio.Task[Any]] = None


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
    global network_policy_task
    try:
        mav.start()
    except Exception as exc:
        mav.start_error = f"mavlink startup failed: {exc}"
        logger.exception("MAVLink startup failed")
    if network_manager.nmcli_available:
        with suppress(Exception):
            await asyncio.to_thread(network_manager.apply_priority_policy)
        network_policy_task = asyncio.create_task(network_policy_worker())
    else:
        network_manager._set_last_apply("error", "nmcli not found", None)


@app.on_event("shutdown")
async def shutdown_event() -> None:
    global network_policy_task
    with suppress(Exception):
        mav.stop()
    if network_policy_task is not None:
        network_policy_task.cancel()
        with suppress(asyncio.CancelledError):
            await network_policy_task
        network_policy_task = None


async def network_policy_worker() -> None:
    while True:
        try:
            await asyncio.to_thread(network_manager.apply_priority_policy)
        except Exception as exc:
            network_manager._set_last_apply("error", str(exc), None)
        await asyncio.sleep(network_manager.monitor_interval_sec)


@app.get("/api/health")
async def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "connected": mav.connected,
        "mav_start_error": mav.start_error,
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


@app.get("/api/network/status")
async def network_status() -> Dict[str, Any]:
    return await asyncio.to_thread(network_manager.status)


@app.get("/api/network/scan")
async def network_scan() -> Dict[str, Any]:
    try:
        networks = await asyncio.to_thread(network_manager.scan_wifi, True)
        return {"ok": True, "networks": networks}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/network/policy")
async def network_policy_get() -> Dict[str, Any]:
    return {"ok": True, "policy": await asyncio.to_thread(network_manager.get_policy)}


@app.put("/api/network/policy")
async def network_policy_update(req: NetworkPolicyRequest) -> Dict[str, Any]:
    try:
        policy = await asyncio.to_thread(
            network_manager.set_policy,
            req.wifi_ssid,
            req.tethering_ssid,
        )
        return {"ok": True, "policy": policy}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/network/connect")
async def network_connect(req: NetworkConnectRequest) -> Dict[str, Any]:
    try:
        result = await asyncio.to_thread(
            network_manager.connect,
            req.role,
            req.ssid,
            req.password,
            req.persist_to_policy,
        )
        return {"ok": True, **result}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/network/apply-priority")
async def network_apply_priority() -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(network_manager.apply_priority_policy)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.websocket("/ws/telemetry")
async def ws_telemetry(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(mav.snapshot())
            await asyncio.sleep(0.2)
    except WebSocketDisconnect:
        return


@app.websocket("/ws/video/publish")
async def ws_video_publish(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("bytes") is not None:
                await video_hub.publish(msg["bytes"])
            elif msg.get("type") == "websocket.disconnect":
                return
    except WebSocketDisconnect:
        return


@app.websocket("/ws/video/stream")
async def ws_video_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    await video_hub.add_viewer(websocket)
    try:
        while True:
            # Viewers may send ping text to keep this loop responsive to disconnect.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await video_hub.remove_viewer(websocket)
