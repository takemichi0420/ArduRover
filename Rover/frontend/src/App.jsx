import { useEffect, useMemo, useRef, useState } from "react";
import { MapContainer, TileLayer, CircleMarker, Popup, useMap } from "react-leaflet";

const DEFAULT_HOST = window.location.hostname || "127.0.0.1";
const API_PROTO = window.location.protocol === "https:" ? "https" : "http";
const WS_PROTO = window.location.protocol === "https:" ? "wss" : "ws";
const API_BASE = import.meta.env.VITE_API_BASE ?? `${API_PROTO}://${DEFAULT_HOST}:8000`;
const WS_URL = import.meta.env.VITE_WS_URL ?? `${WS_PROTO}://${DEFAULT_HOST}:8000/ws/telemetry`;
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

function fmt(value, digits = 3) {
  if (value === null || value === undefined) return "-";
  return Number(value).toFixed(digits);
}

async function postManualControl(payload) {
  const res = await fetch(`${API_BASE}/api/manual-control`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? "manual-control failed");
  }
}

function hasCoord(lat, lon) {
  return Number.isFinite(lat) && Number.isFinite(lon) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180 && !(lat === 0 && lon === 0);
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

export default function App() {
  const [telemetry, setTelemetry] = useState(null);
  const [status, setStatus] = useState("connecting");
  const [apiStatus, setApiStatus] = useState("idle");
  const [error, setError] = useState("");
  const [throttle, setThrottle] = useState(0);
  const [steer, setSteer] = useState(0);
  const [followMap, setFollowMap] = useState(true);
  const [baseMap, setBaseMap] = useState("satellite");

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

  const sendCurrent = async () => {
    try {
      await postManualControl({
        x: steer,
        y: throttle,
        z: 500,
        r: 0,
        buttons: 0
      });
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const quickCommand = async (x, y) => {
    try {
      await postManualControl({ x, y, z: 500, r: 0, buttons: 0 });
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  return (
    <main className="page">
      <section className="panel left">
        <h1>Rover GCS</h1>
        <p className="meta">WebSocket: {status}</p>
        <p className="meta">HTTP Poll: {apiStatus}</p>
        <p className="meta">SYS/COMP: {telemetry?.target_system ?? "-"} / {telemetry?.target_component ?? "-"}</p>

        <div className="card">
          <h2>Telemetry</h2>
          <p>Lat/Lon: {fmt(lat, 6)} / {fmt(lon, 6)}</p>
          <p>Home: {fmt(homeLat, 6)} / {fmt(homeLon, 6)}</p>
          <p>Rel Alt: {fmt(telemetry?.position?.relative_alt_m, 2)} m</p>
          <p>Yaw: {fmt(telemetry?.attitude?.yaw_rad, 3)} rad</p>
          <p>GPS Fix: {telemetry?.gps?.fix_type ?? "-"} (Sat {telemetry?.gps?.satellites_visible ?? "-"})</p>
          <p>Battery: {fmt(telemetry?.battery?.voltage_v, 2)} V ({telemetry?.battery?.remaining_pct ?? "-"}%)</p>
          <div className="map-mode">
            <button
              type="button"
              className={baseMap === "default" ? "active" : ""}
              onClick={() => setBaseMap("default")}
            >
              地図: {BASE_MAPS.default.label}
            </button>
            <button
              type="button"
              className={baseMap === "satellite" ? "active" : ""}
              onClick={() => setBaseMap("satellite")}
            >
              地図: {BASE_MAPS.satellite.label}
            </button>
            <button
              type="button"
              className={baseMap === "topo" ? "active" : ""}
              onClick={() => setBaseMap("topo")}
            >
              地図: {BASE_MAPS.topo.label}
            </button>
          </div>
          <div className="actions" style={{ marginTop: 8 }}>
            <button onClick={() => setFollowMap((v) => !v)}>
              {followMap ? "Map Follow: ON" : "Map Follow: OFF"}
            </button>
          </div>
        </div>

        <div className="card">
          <h2>Manual Control</h2>
          <label>
            Throttle (Y): {throttle}
            <input type="range" min="-1000" max="1000" value={throttle} onChange={(e) => setThrottle(Number(e.target.value))} />
          </label>
          <label>
            Steering (X): {steer}
            <input type="range" min="-1000" max="1000" value={steer} onChange={(e) => setSteer(Number(e.target.value))} />
          </label>
          <div className="actions">
            <button onClick={sendCurrent}>Send</button>
            <button onClick={() => quickCommand(0, 400)}>Forward</button>
            <button onClick={() => quickCommand(0, -400)}>Reverse</button>
            <button onClick={() => quickCommand(-400, 0)}>Left</button>
            <button onClick={() => quickCommand(400, 0)}>Right</button>
            <button onClick={() => quickCommand(0, 0)}>Stop</button>
          </div>
          {error ? <p className="error">{error}</p> : null}
        </div>
      </section>

      <section className="panel right">
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
      </section>
    </main>
  );
}
