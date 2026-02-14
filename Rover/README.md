# Rover GCS

SITL (ArduRover) と Web ベース GCS の最小構成です。

## 構成

- `backend`: FastAPI + pymavlink
- `frontend`: React + Vite + Leaflet

## 1. SITL 起動

`sim_vehicle.py` を起動します。

```bash
sim_vehicle.py -v Rover -f rover-skid -w --console --map --out=127.0.0.1:14551 -L Kawachi
```

ずれや混線を避けるため、SITLは `--out=127.0.0.1:14551` のように専用ポートを指定してください。  
バックエンドは同じポートを `MAVLINK_CONNECTION` で受信します。

SITLの起動座標を固定したい場合は `--location`（または `-L`）を指定してください。  
フロントエンドは `GLOBAL_POSITION_INT` が来るまで `HOME_POSITION` を地図中心に使います。

## 2. Backend 起動

Python 3.11 を使用します。

```bash
cd workshop/Rover/backend
./setup_venv.sh
source rvenv/bin/activate
MAVLINK_CONNECTION="udpin:127.0.0.1:14551" uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

ポートを変更したい場合は環境変数で指定できます。

```bash
MAVLINK_CONNECTION="udpin:127.0.0.1:14552" uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

`python3.11` が見つからない場合（現在の環境は 3.10.12）:

```bash
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv
```

バージョン確認（`3.11.x` の final 推奨）:

```bash
python3.11 --version
```

`3.11.0rc1` の場合は RC 版です。運用では final 版へ更新してください。

確認:

```bash
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/telemetry
```

## 3. Frontend 起動

Node.js 18+ が必要です。

```bash
node -v
npm -v
```

`node -v` が `v12` の場合は先に更新してください（例: `nvm` で `20` を導入）。

```bash
cd workshop/Rover/frontend
npm install
npm run dev
```

ブラウザ: `http://127.0.0.1:5173`

## エンドポイント

- `GET /api/health`: 接続状態
- `GET /api/telemetry`: 現在のスナップショット
- `POST /api/manual-control`: `MANUAL_CONTROL` 送信
- `WS /ws/telemetry`: 5Hz でテレメトリ配信

## 注意

- 手動操作が効かない場合は、SITL 側のモードや ARM 状態を確認してください。
- `target_system=0` のままなら、正しいハートビートをまだ取得できていません。
