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

Python 3.11 以上を使用します。

```bash
cd workshop/Rover/backend
./setup_venv.sh
source rvenv/bin/activate
MAVLINK_CONNECTION="udpin:127.0.0.1:14551" uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Pi上でネットワーク優先制御も使う場合:

```bash
WIFI_INTERFACE="wlan0" \
NETWORK_POLICY_PATH="$HOME/.config/rover-gcs/network_policy.json" \
NETWORK_POLICY_INTERVAL_SEC="20" \
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

ポートを変更したい場合は環境変数で指定できます。

```bash
MAVLINK_CONNECTION="udpin:127.0.0.1:14552" uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

`python3.11` が見つからない場合:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv
```

バージョン確認（`3.11+` の final 推奨）:

```bash
python3 --version
```

Debian bookworm のように `python3.11` がある環境では `python3.11 python3.11-venv` でも構いません。

確認:

```bash
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/telemetry
```

補足:

- backend は FC 接続後、MAVLink GCS `HEARTBEAT` を 1Hz で常時送信します
- failsafe 関連の `STATUSTEXT` を監視し、terminal に理由を出力します
- backend は `last_seen_at` を FC からの最終受信時刻として更新し、既定 `2.5s` 無通信なら `Pi-FC通信断 FailSafe` を出します
- frontend では failsafe 起動中に地図上へ理由と状態をオーバーレイ表示します
- frontend では `HTTP poll failed` を即時、`WebSocket closed/error` を 5 秒継続したときだけローカル failsafe として表示します
- frontend の telemetry WebSocket は切断時に自動再接続します
- 優先順は `RC > MissionPlanner(GCS) = Web GCS` です。MissionPlanner と Web GCS は同列として扱い、MissionPlanner が開いているだけでは Web Control を止めません
- backend は既定 `CH7 <= 1300` を `RC優先` とみなし、その間は Web Control の `RC_CHANNELS_OVERRIDE` を解放して拒否します
- `Rover/mav.parm` では `RC7_OPTION = 46` (`RC_OVERRIDE_ENABLE`) を使います。CH7 LOW で MissionPlanner/Web GCS の override をFC側で禁止し、CH7 HIGH でGCS overrideを許可します
- `CH5` は `MODE_CH` として使い、3ポジションで `MANUAL / AUTO / GUIDED` を切り替えます
- `CH8` は2ポジションの緊急HOLDとして使い、`RC8_OPTION = 54` (`HOLD`) でON側は必ず `HOLD` に入ります
- frontend には地図上左上に `緊急 HOLD` ボタンがあり、override を解放して `HOLD` モードへ切り替えます

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

## 3.1 Pi 起動時に backend / frontend を自動起動

Pi では `systemd` を使うのが素直です。

前提:

- repo は `~/GitHub/workshop` に clone 済み
- backend の venv は `workshop/Rover/backend/setup_venv.sh` で作成済み
- frontend の `dist` は repo に配置済み

unit ファイルは repo に同梱しています。

- backend: `Rover/backend/rover-backend.service`
- frontend: `Rover/frontend/rover-frontend.service`

インストール:

```bash
cd ~/GitHub/workshop
sudo cp Rover/backend/rover-backend.service /etc/systemd/system/
sudo cp Rover/frontend/rover-frontend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable rover-backend.service rover-frontend.service
sudo systemctl start rover-backend.service rover-frontend.service
```

確認:

```bash
systemctl status rover-backend.service
systemctl status rover-frontend.service
```

ログ確認:

```bash
journalctl -u rover-backend.service -f
journalctl -u rover-frontend.service -f
```

補足:

- backend は `http://<pi-ip>:8000`
- frontend は `http://<pi-ip>:5173`
- `MAVLINK_CONNECTION` や `WIFI_INTERFACE` を変える場合は `Rover/backend/rover-backend.service` を編集してから `daemon-reload` します
- frontend は `Rover/frontend/dist` を静的配信します。`5173` を変える場合は `Rover/frontend/rover-frontend.service` を編集します
- frontend のソースを変更した場合は、配備前に `npm run build` で `dist` を更新してください

## エンドポイント

- `GET /api/health`: 接続状態
- `GET /api/telemetry`: 現在のスナップショット
- `POST /api/manual-control`: `RC_CHANNELS_OVERRIDE` 送信
- `POST /api/manual-control/release`: Web Control の override を解除して RC へ戻す
- `POST /api/action/hold`: override を解除して `HOLD` モードへ切り替える
- `WS /ws/telemetry`: 5Hz でテレメトリ配信
- `WS /ws/video/publish`: スマホからJPEGフレームを受信
- `WS /ws/video/stream`: GCSへ映像フレームを配信
- `GET /api/network/status`: Pi の接続状態、優先ポリシー、可視SSID
- `GET /api/network/scan`: 可視SSIDの再スキャン
- `PUT /api/network/policy`: 優先Wi-Fi SSID / テザリング SSID を保存
- `POST /api/network/connect`: SSID + パスワードで接続テスト（成功時は保存）
- `POST /api/network/apply-priority`: Wi-Fi優先フォールバックを即時適用

## 4. スマホカメラ映像を表示

1. GCSを開いたPCで backend / frontend を起動
2. スマホで `http://<frontend-host>:5173/phone-camera.html` を開く
3. `Start` を押す（カメラ許可が必要）
4. GCS画面の `Camera` パネルに映像が表示される

同一LANで使う場合は、スマホから backend の `8000` ポートへ到達できる必要があります。

## 5. Pi ネットワーク優先制御（Wi-Fi優先 / テザリングフォールバック）

1. Web GCS の `Network Priority (Pi)` カードを開く
2. `Wi-Fi (優先) SSID` と `テザリング SSID` を入力
3. 必要ならパスワードを入力して `接続テスト` を実行
4. `優先設定を保存` を実行
5. `優先ポリシー適用` を実行

仕様:

- Wi-Fi SSID が見える場合は Wi-Fi を優先
- Wi-Fi が見えない場合は テザリング SSID にフォールバック
- バックエンドは定期的に優先ポリシーを再評価（既定20秒）

## GCS Failsafe の既定値

`workshop/Rover/mav.parm` の既定値は以下です。

- `FS_ACTION = 2`
- `FS_GCS_ENABLE = 1`

意味:

- `FS_ACTION = 2` は `Hold`
- `FS_GCS_ENABLE = 1` は GCS failsafe を常時有効化します
- そのため、`AUTO` ミッション中を含めて GCS failsafe 時に `Hold` へ切り替わります

## 注意

- 手動操作が効かない場合は、SITL 側のモードや ARM 状態を確認してください。
- `target_system=0` のままなら、正しいハートビートをまだ取得できていません。
- Pi/companion が落ちたときは、GCS heartbeat 断と RC override 断のどちらが先に効くかで failsafe 理由が変わることがあります。
