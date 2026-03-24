import { useEffect, useMemo, useRef, useState } from "react";
import { MapContainer, TileLayer, CircleMarker, Popup, useMap } from "react-leaflet";

const DEFAULT_HOST = window.location.hostname || "127.0.0.1";
const API_PROTO = window.location.protocol === "https:" ? "https" : "http";
const WS_PROTO = window.location.protocol === "https:" ? "wss" : "ws";
const API_BASE = import.meta.env.VITE_API_BASE ?? `${API_PROTO}://${DEFAULT_HOST}:8000`;
const WS_URL = import.meta.env.VITE_WS_URL ?? `${WS_PROTO}://${DEFAULT_HOST}:8000/ws/telemetry`;
const GO2RTC_WEBRTC_URL = "http://100.121.120.31:1984/webrtc.html?src=cam&media=video";
const PHONE_CAMERA_PAGE_URL = `${window.location.origin}/phone-camera.html`;
const DEFAULT_LAT = Number(import.meta.env.VITE_DEFAULT_LAT ?? 35.0);
const DEFAULT_LON = Number(import.meta.env.VITE_DEFAULT_LON ?? 139.0);
const BASE_MAPS = {
  default: {
    label: "標準",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
  },
  satellite: {
    label: "衛星",
    attribution: "Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
  },
  topo: {
    label: "等高線",
    attribution:
      "Map data: &copy; OpenStreetMap contributors, SRTM | Map style: &copy; OpenTopoMap (CC-BY-SA)",
    url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png"
  }
};
const LOW_BATTERY_THRESHOLD_PCT = 30;

function fmt(value, digits = 3) {
  if (value === null || value === undefined) return "-";
  return Number(value).toFixed(digits);
}

async function readError(res, fallback) {
  const body = await res.json().catch(() => ({}));
  throw new Error(body.detail ?? fallback);
}

async function postManualControl(payload) {
  const res = await fetch(`${API_BASE}/api/manual-control`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    await readError(res, "manual-control failed");
  }
}

async function fetchNetworkStatus() {
  const res = await fetch(`${API_BASE}/api/network/status`);
  if (!res.ok) {
    await readError(res, "network status failed");
  }
  return res.json();
}

async function fetchNetworkScan() {
  const res = await fetch(`${API_BASE}/api/network/scan`);
  if (!res.ok) {
    await readError(res, "network scan failed");
  }
  return res.json();
}

async function putNetworkPolicy(payload) {
  const res = await fetch(`${API_BASE}/api/network/policy`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    await readError(res, "network policy update failed");
  }
  return res.json();
}

async function postNetworkConnect(payload) {
  const res = await fetch(`${API_BASE}/api/network/connect`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    await readError(res, "network connect failed");
  }
  return res.json();
}

async function postApplyPriorityPolicy() {
  const res = await fetch(`${API_BASE}/api/network/apply-priority`, {
    method: "POST"
  });
  if (!res.ok) {
    await readError(res, "network apply-priority failed");
  }
  return res.json();
}

function hasCoord(lat, lon) {
  return Number.isFinite(lat) && Number.isFinite(lon) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180 && !(lat === 0 && lon === 0);
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function MapAutoCenter({ center, enabled }) {
  const map = useMap();
  const prevCenterRef = useRef(null);

  useEffect(() => {
    if (!enabled || !center) return;

    const prev = prevCenterRef.current;
    const movedEnough =
      !prev ||
      Math.abs(prev[0] - center[0]) > 1e-6 ||
      Math.abs(prev[1] - center[1]) > 1e-6;

    if (movedEnough) {
      map.setView(center, map.getZoom(), { animate: false });
      prevCenterRef.current = center;
    }
  }, [center, enabled, map]);

  return null;
}

function JoystickPad({ disabled, xNorm, yNorm, onChange, onRelease }) {
  const padRef = useRef(null);
  const activePointerRef = useRef(null);
  const radius = 58;

  const updateFromClient = (clientX, clientY) => {
    const pad = padRef.current;
    if (!pad) return;

    const rect = pad.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;

    let dx = clientX - centerX;
    let dy = clientY - centerY;
    const mag = Math.hypot(dx, dy);
    if (mag > radius && mag > 0) {
      const scale = radius / mag;
      dx *= scale;
      dy *= scale;
    }

    onChange({
      xNorm: clamp(dx / radius, -1, 1),
      yNorm: clamp(-dy / radius, -1, 1)
    });
  };

  const onPointerDown = (event) => {
    if (disabled) return;
    activePointerRef.current = event.pointerId;
    event.currentTarget.setPointerCapture(event.pointerId);
    updateFromClient(event.clientX, event.clientY);
  };

  const onPointerMove = (event) => {
    if (disabled || activePointerRef.current !== event.pointerId) return;
    updateFromClient(event.clientX, event.clientY);
  };

  const release = (event) => {
    if (activePointerRef.current !== event.pointerId) return;
    activePointerRef.current = null;
    onRelease();
  };

  return (
    <div
      ref={padRef}
      className={`joystick-pad${disabled ? " disabled" : ""}`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={release}
      onPointerCancel={release}
    >
      <div className="joystick-ring" />
      <div
        className="joystick-knob"
        style={{
          transform: `translate(calc(-50% + ${xNorm * radius}px), calc(-50% + ${-yNorm * radius}px))`
        }}
      />
    </div>
  );
}

export default function App() {
  const [telemetry, setTelemetry] = useState(null);
  const [status, setStatus] = useState("connecting");
  const [apiStatus, setApiStatus] = useState("idle");
  const [error, setError] = useState("");
  const [followMap, setFollowMap] = useState(true);
  const [baseMap, setBaseMap] = useState("satellite");
  const [webControlEnabled, setWebControlEnabled] = useState(false);
  const [stick, setStick] = useState({ xNorm: 0, yNorm: 0 });
  const [networkStatus, setNetworkStatus] = useState(null);
  const [networkList, setNetworkList] = useState([]);
  const [networkBusy, setNetworkBusy] = useState(false);
  const [networkMessage, setNetworkMessage] = useState("");
  const [networkError, setNetworkError] = useState("");
  const [primarySsid, setPrimarySsid] = useState("");
  const [primaryPassword, setPrimaryPassword] = useState("");
  const [secondarySsid, setSecondarySsid] = useState("");
  const [secondaryPassword, setSecondaryPassword] = useState("");
  const sendingRef = useRef(false);

  useEffect(() => {
    const mobileLike =
      window.matchMedia("(max-width: 960px)").matches ||
      /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
    if (!mobileLike || !window.screen?.orientation?.lock) return;
    window.screen.orientation.lock("landscape").catch(() => {});
  }, []);

  useEffect(() => {
    const ws = new WebSocket(WS_URL);

    ws.onopen = () => setStatus("connected");
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setTelemetry(data);
        setError("");
      } catch (e) {
        setError(`WS parse error: ${String(e.message || e)}`);
      }
    };
    ws.onerror = () => setStatus("error");
    ws.onclose = () => setStatus("closed");

    return () => ws.close();
  }, []);

  useEffect(() => {
    let timerId;
    let stopped = false;

    const poll = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/telemetry`);
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        const data = await res.json();
        if (!stopped) {
          setTelemetry(data);
          setApiStatus("ok");
        }
      } catch (e) {
        if (!stopped) {
          setApiStatus("error");
          setError((prev) => prev || `API poll failed: ${String(e.message || e)}`);
        }
      } finally {
        if (!stopped) {
          timerId = window.setTimeout(poll, 1000);
        }
      }
    };

    poll();
    return () => {
      stopped = true;
      if (timerId) {
        window.clearTimeout(timerId);
      }
    };
  }, []);

  const syncPolicyInputs = (policy) => {
    setPrimarySsid((prev) => prev || (policy?.primary_ssid ?? policy?.wifi_ssid ?? ""));
    setSecondarySsid((prev) => prev || (policy?.secondary_ssid ?? policy?.tethering_ssid ?? ""));
  };

  const refreshNetworkStatus = async () => {
    const data = await fetchNetworkStatus();
    setNetworkStatus(data);
    setNetworkList(Array.isArray(data?.visible_networks) ? data.visible_networks : []);
    syncPolicyInputs(data?.policy);
    setNetworkError("");
    return data;
  };

  const refreshNetworkScan = async () => {
    const scan = await fetchNetworkScan();
    setNetworkList(Array.isArray(scan?.networks) ? scan.networks : []);
    setNetworkError("");
    return scan;
  };

  useEffect(() => {
    let stopped = false;
    let timerId;

    const poll = async () => {
      try {
        await refreshNetworkStatus();
      } catch (e) {
        if (!stopped) {
          setNetworkError(`Network status failed: ${String(e.message || e)}`);
        }
      } finally {
        if (!stopped) {
          timerId = window.setTimeout(poll, 8000);
        }
      }
    };

    poll();
    return () => {
      stopped = true;
      if (timerId) {
        window.clearTimeout(timerId);
      }
    };
  }, []);

  const handleSavePolicy = async () => {
    if (!primarySsid.trim() && !secondarySsid.trim()) {
      setNetworkError("第1候補または第2候補の SSID を少なくとも1つ入力してください");
      return;
    }
    setNetworkBusy(true);
    setNetworkError("");
    setNetworkMessage("");
    try {
      await putNetworkPolicy({
        primary_ssid: primarySsid.trim(),
        secondary_ssid: secondarySsid.trim()
      });
      await refreshNetworkStatus();
      setNetworkMessage("優先設定を保存しました（第1候補 / 第2候補）");
    } catch (e) {
      setNetworkError(String(e.message || e));
    } finally {
      setNetworkBusy(false);
    }
  };

  const handleConnectRole = async (role) => {
    const isPrimary = role === "primary";
    const ssid = isPrimary ? primarySsid.trim() : secondarySsid.trim();
    const password = isPrimary ? primaryPassword : secondaryPassword;
    if (!ssid) {
      setNetworkError(`${isPrimary ? "第1候補" : "第2候補"} SSID を入力してください`);
      return;
    }

    setNetworkBusy(true);
    setNetworkError("");
    setNetworkMessage("");
    try {
      const result = await postNetworkConnect({
        role,
        ssid,
        password,
        persist_to_policy: true
      });
      await refreshNetworkStatus();
      if (isPrimary) {
        setPrimaryPassword("");
      } else {
        setSecondaryPassword("");
      }
      setNetworkMessage(`${isPrimary ? "第1候補" : "第2候補"} 接続成功: ${result.active?.ssid ?? ssid}`);
    } catch (e) {
      setNetworkError(String(e.message || e));
    } finally {
      setNetworkBusy(false);
    }
  };

  const handleApplyPriority = async () => {
    setNetworkBusy(true);
    setNetworkError("");
    setNetworkMessage("");
    try {
      const result = await postApplyPriorityPolicy();
      await refreshNetworkStatus();
      setNetworkMessage(
        result?.ok
          ? `優先ポリシー適用: ${result.target_ssid ?? result.active?.ssid ?? "-"}`
          : `優先ポリシー待機: ${result.message ?? "configured SSID not visible"}`
      );
    } catch (e) {
      setNetworkError(String(e.message || e));
    } finally {
      setNetworkBusy(false);
    }
  };

  const handleScan = async () => {
    setNetworkBusy(true);
    setNetworkError("");
    setNetworkMessage("");
    try {
      await refreshNetworkScan();
      setNetworkMessage("周辺ネットワークを更新しました");
    } catch (e) {
      setNetworkError(String(e.message || e));
    } finally {
      setNetworkBusy(false);
    }
  };

  const lat = telemetry?.position?.lat_deg;
  const lon = telemetry?.position?.lon_deg;
  const homeLat = telemetry?.home?.lat_deg;
  const homeLon = telemetry?.home?.lon_deg;

  const mapCenter = useMemo(() => {
    if (hasCoord(lat, lon)) return [lat, lon];
    if (hasCoord(homeLat, homeLon)) return [homeLat, homeLon];
    return [DEFAULT_LAT, DEFAULT_LON];
  }, [lat, lon, homeLat, homeLon]);
  const selectedBaseMap = BASE_MAPS[baseMap] ?? BASE_MAPS.satellite;
  const steerValue = Math.round(stick.xNorm * 1000);
  const throttleValue = Math.round(stick.yNorm * 1000);
  const batteryRemainingPct = Number(telemetry?.battery?.remaining_pct);
  const hasBatteryRemainingPct = Number.isFinite(batteryRemainingPct);
  const lowBattery = hasBatteryRemainingPct && batteryRemainingPct <= LOW_BATTERY_THRESHOLD_PCT;
  const batteryDisplayText = hasBatteryRemainingPct ? `${Math.round(batteryRemainingPct)}%` : "-";

  const sendManual = async (steer, throttle) => {
    if (!webControlEnabled) {
      setError("Enable Web Control first");
      return;
    }
    await postManualControl({
      x: 0,
      y: steer,
      z: throttle,
      r: 0,
      buttons: 0
    });
  };

  const quickCommand = async (steer, throttle) => {
    try {
      await sendManual(steer, throttle);
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  useEffect(() => {
    if (!webControlEnabled) {
      setStick({ xNorm: 0, yNorm: 0 });
      postManualControl({ x: 0, y: 0, z: 0, r: 0, buttons: 0 }).catch(() => {});
      return;
    }

    let cancelled = false;
    const sendTick = async () => {
      if (cancelled || sendingRef.current) return;
      sendingRef.current = true;
      try {
        await postManualControl({ x: 0, y: steerValue, z: throttleValue, r: 0, buttons: 0 });
      } catch (e) {
        if (!cancelled) {
          setError(String(e.message || e));
        }
      } finally {
        sendingRef.current = false;
      }
    };

    sendTick();
    const timerId = window.setInterval(sendTick, 150);
    return () => {
      cancelled = true;
      window.clearInterval(timerId);
    };
  }, [webControlEnabled, steerValue, throttleValue]);

  return (
    <>
      <div className="rotate-lock-overlay">
        スマホでは横画面で利用してください
      </div>
      <main className="page">
        <section className="top-stage">
          <section className="pane camera-pane">
            <div className="pane-head">
              <h1>Rover GCS</h1>
              <p className="meta">WebSocket: {status}</p>
              <p className="meta">HTTP Poll: {apiStatus}</p>
              <p className="meta">SYS/COMP: {telemetry?.target_system ?? "-"} / {telemetry?.target_component ?? "-"}</p>
            </div>
            <iframe
              className="camera-frame camera-iframe camera-stage-frame"
              src={GO2RTC_WEBRTC_URL}
              title="go2rtc camera stream"
              allow="autoplay; fullscreen; picture-in-picture; microphone; camera"
            />
            <a className="camera-link" href={GO2RTC_WEBRTC_URL} target="_blank" rel="noreferrer">
              {GO2RTC_WEBRTC_URL}
            </a>
            <a className="camera-link" href={PHONE_CAMERA_PAGE_URL} target="_blank" rel="noreferrer">
              {PHONE_CAMERA_PAGE_URL}
            </a>
          </section>

          <section className="pane map-pane">
            <div className="map-shell">
              <MapContainer center={[DEFAULT_LAT, DEFAULT_LON]} zoom={18} className="map" scrollWheelZoom>
                <MapAutoCenter center={mapCenter} enabled={followMap} />
                <TileLayer
                  attribution={selectedBaseMap.attribution}
                  url={selectedBaseMap.url}
                />
                {hasCoord(lat, lon) ? (
                  <CircleMarker center={[lat, lon]} radius={8} pathOptions={{ color: "#ef4444" }}>
                    <Popup>Rover position</Popup>
                  </CircleMarker>
                ) : null}
                {!hasCoord(lat, lon) && hasCoord(homeLat, homeLon) ? (
                  <CircleMarker center={[homeLat, homeLon]} radius={7} pathOptions={{ color: "#0ea5e9" }}>
                    <Popup>SITL home position</Popup>
                  </CircleMarker>
                ) : null}
              </MapContainer>

              <div className={`battery-overlay${lowBattery ? " low" : ""}`}>
                <span>Battery</span>
                <strong>{batteryDisplayText}</strong>
              </div>

              <div className="map-overlay map-overlay-top-right">
                <div className="map-mode">
                  <button
                    type="button"
                    className={baseMap === "default" ? "active" : ""}
                    onClick={() => setBaseMap("default")}
                  >
                    {BASE_MAPS.default.label}
                  </button>
                  <button
                    type="button"
                    className={baseMap === "satellite" ? "active" : ""}
                    onClick={() => setBaseMap("satellite")}
                  >
                    {BASE_MAPS.satellite.label}
                  </button>
                  <button
                    type="button"
                    className={baseMap === "topo" ? "active" : ""}
                    onClick={() => setBaseMap("topo")}
                  >
                    {BASE_MAPS.topo.label}
                  </button>
                </div>
                <button type="button" onClick={() => setFollowMap((v) => !v)}>
                  {followMap ? "Map Follow: ON" : "Map Follow: OFF"}
                </button>
              </div>

              <div className="map-overlay map-overlay-bottom-left">
                <button
                  type="button"
                  className={`control-toggle${webControlEnabled ? " active" : ""}`}
                  onClick={() => setWebControlEnabled((prev) => !prev)}
                >
                  {webControlEnabled ? "Web Control: ENABLED" : "Web Control: DISABLED"}
                </button>
                <JoystickPad
                  disabled={!webControlEnabled}
                  xNorm={stick.xNorm}
                  yNorm={stick.yNorm}
                  onChange={setStick}
                  onRelease={() => setStick({ xNorm: 0, yNorm: 0 })}
                />
                <p className="meta overlay-meta">Steer/Throttle: {steerValue} / {throttleValue}</p>
              </div>
            </div>
          </section>
        </section>

        <section className="details-scroll">
          <div className="card">
            <h2>Telemetry</h2>
            <p>Lat/Lon: {fmt(lat, 6)} / {fmt(lon, 6)}</p>
            <p>Home: {fmt(homeLat, 6)} / {fmt(homeLon, 6)}</p>
            <p>Rel Alt: {fmt(telemetry?.position?.relative_alt_m, 2)} m</p>
            <p>Yaw: {fmt(telemetry?.attitude?.yaw_rad, 3)} rad</p>
            <p>GPS Fix: {telemetry?.gps?.fix_type ?? "-"} (Sat {telemetry?.gps?.satellites_visible ?? "-"})</p>
            <p>Battery: {fmt(telemetry?.battery?.voltage_v, 2)} V ({batteryDisplayText})</p>
            {lowBattery ? <p className="error">Battery 30%以下: 速やかに帰還してください</p> : null}
          </div>

          <div className="card">
            <h2>Manual Command</h2>
            <div className="actions">
              <button onClick={() => quickCommand(0, 400)}>Forward</button>
              <button onClick={() => quickCommand(0, -400)}>Reverse</button>
              <button onClick={() => quickCommand(-400, 0)}>Left</button>
              <button onClick={() => quickCommand(400, 0)}>Right</button>
              <button onClick={() => quickCommand(0, 0)}>Stop</button>
            </div>
            {error ? <p className="error">{error}</p> : null}
          </div>

          <div className="card">
            <h2>Network Priority (Pi)</h2>
            <p className="meta">nmcli: {networkStatus?.nmcli_available ? "available" : "unavailable"}</p>
            <p className="meta">Interface: {networkStatus?.interface ?? "-"}</p>
            <p className="meta">Active SSID: {networkStatus?.active?.ssid ?? "-"}</p>
            <p className="meta">
              Last Apply: {networkStatus?.last_apply?.status ?? "-"}
              {networkStatus?.last_apply?.message ? ` (${networkStatus.last_apply.message})` : ""}
            </p>

            <div className="network-grid">
              <label>
                第1候補 SSID
                <input
                  type="text"
                  value={primarySsid}
                  onChange={(event) => setPrimarySsid(event.target.value)}
                  placeholder="iphone-hotspot"
                />
              </label>
              <label>
                第1候補 Password
                <input
                  type="password"
                  value={primaryPassword}
                  onChange={(event) => setPrimaryPassword(event.target.value)}
                  placeholder="********"
                />
              </label>
              <label>
                第2候補 SSID
                <input
                  type="text"
                  value={secondarySsid}
                  onChange={(event) => setSecondarySsid(event.target.value)}
                  placeholder="backup-wifi"
                />
              </label>
              <label>
                第2候補 Password
                <input
                  type="password"
                  value={secondaryPassword}
                  onChange={(event) => setSecondaryPassword(event.target.value)}
                  placeholder="********"
                />
              </label>
            </div>

            <div className="network-actions">
              <button type="button" disabled={networkBusy} onClick={handleSavePolicy}>
                優先設定を保存
              </button>
              <button type="button" disabled={networkBusy} onClick={() => handleConnectRole("primary")}>
                第1候補接続テスト
              </button>
              <button type="button" disabled={networkBusy} onClick={() => handleConnectRole("secondary")}>
                第2候補接続テスト
              </button>
              <button type="button" disabled={networkBusy} onClick={handleApplyPriority}>
                優先ポリシー適用
              </button>
              <button type="button" disabled={networkBusy} onClick={handleScan}>
                周辺ネットワーク再検索
              </button>
            </div>

            <div className="network-scan-list">
              {networkList.slice(0, 8).map((net) => (
                <div key={net.ssid} className="network-scan-row">
                  <div>
                    <strong>{net.ssid}</strong>
                    <span className="meta"> ({net.signal ?? "-"}%) {net.security || "open"}</span>
                  </div>
                  <div className="network-scan-buttons">
                    <button type="button" onClick={() => setPrimarySsid(net.ssid)}>第1に設定</button>
                    <button type="button" onClick={() => setSecondarySsid(net.ssid)}>第2に設定</button>
                  </div>
                </div>
              ))}
            </div>

            {networkMessage ? <p className="meta">{networkMessage}</p> : null}
            {networkError ? <p className="error">{networkError}</p> : null}
          </div>
        </section>
      </main>
    </>
  );
}
