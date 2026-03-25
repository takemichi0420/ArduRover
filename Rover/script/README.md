# Rover LiDAR Safety Script

このディレクトリには、ArduRover 向けの Lua 安全監視スクリプトを格納しています。

## 含まれるファイル

- `rover_lidar_safety.lua`
  - 障害物が近すぎる場合に Rover を停止します
  - LiDAR の更新が止まった場合に Rover を停止します
- `01_enable_scripting.parm`
  - Lua scripting を有効にするための最小パラメータ例です
- `02_rover_lidar_safety.parm`
  - 前方 1 台監視用の設定例です
- `03_rover_lidar_safety_omni.parm`
  - 全方位監視用の設定例です

## 動作概要

このスクリプトには 2 つの監視モードがあります。

- 前方監視モード:
  - `RSTM_MULTI=0`
  - `RSTOP_INST=1` は `RNGFND1_*` を意味します
  - 従来どおり前方 1 台の LiDAR を監視します
- 全方位監視モード:
  - `RSTM_MULTI=1`
  - 水平面に設定されたすべてのレンジファインダを監視します
  - 対象方位は yaw 0, 45, 90, 135, 180, 225, 270, 315 deg です

どちらのモードでも、以下の条件で `RSTOP_MODE` へ切り替えます。

- 計測距離が `RSTOP_DIST_M` 以下になったとき
- LiDAR の更新停止時間が `RSTOP_TOUT_MS` を超えたとき
- レンジファインダ状態が `NotConnected` または `NoData` になったとき

既定の停止モードは Rover の `HOLD (4)` です。

## セットアップ手順

1. `rover_lidar_safety.lua` をオートパイロットの script ディレクトリへ配置します。
   - Pixhawk 6C Mini では microSD 上の以下へ配置します:
     - `/APM/scripts/rover_lidar_safety.lua`
2. scripting を有効にします。
   - `01_enable_scripting.parm` を読み込むか、以下を設定します:
     - `SCR_ENABLE = 1`
     - `SCR_HEAP_SIZE = 200000`
3. オートパイロットを再起動するか、scripting を再起動します。
4. `RSTOP_*` と `RSTM_*` のパラメータが作成されることを確認します。
5. ArduPilot 側で LiDAR を設定します。
   - 前方監視の例:
     - `RNGFND1_TYPE = <使用する LiDAR ドライバ>`
     - `RNGFND1_ORIENT = 0`
     - `RNGFND1_MIN = <センサ最小距離>`
     - `RNGFND1_MAX = <センサ最大距離>`
   - 全方位監視の例:
     - 水平面の各 `RNGFND*` に正しい yaw 方位を設定します
6. 以下のどちらかの設定例を読み込みます。
   - 前方監視: `02_rover_lidar_safety.parm`
   - 全方位監視: `03_rover_lidar_safety_omni.parm`
7. 起動後に GCS メッセージを確認します。
   - `RSTOP: loaded ...`
   - 異常時: `RSTOP: ... -> mode 4`

## Pixhawk 6C Mini 向けメモ

- このスクリプト自体は Pixhawk 6C Mini 向けに特別な修正は不要です。
- Lua script は `/APM/scripts` から読み込まれるため、microSD が必要です。
- LiDAR の配線方法に応じてドライバとポート設定を合わせてください。
  - UART LiDAR の例:
    - `SERIALx_PROTOCOL`
    - `SERIALx_BAUD`
    - `RNGFND1_TYPE`
  - I2C / CAN LiDAR の例:
    - `RNGFND1_TYPE`
    - センサ固有パラメータ
- `RNGFND1_TYPE` や `SERIALx_*` の具体値は LiDAR の機種依存です。

## 主なパラメータ

- `RSTOP_ENABLE`
  - `1`: 有効
  - `0`: 無効
- `RSTOP_INST`
  - 前方監視モードで監視する 1-based のレンジファインダ番号
- `RSTM_MULTI`
  - `0`: `RSTOP_INST` だけを監視する
  - `1`: 水平面のレンジファインダをすべて監視する
- `RSTOP_DIST_M`
  - 障害物停止距離 [m]
- `RSTOP_TOUT_MS`
  - LiDAR 更新停止の判定時間 [ms]
- `RSTOP_MODE`
  - 停止時に切り替える Rover モード番号
- `RSTOP_REQ_ARM`
  - `1`: ARM 中のみ監視
  - `0`: 常時監視
- `RSTOP_GRACE_MS`
  - ARM 直後に timeout / no-data を猶予する時間 [ms]

## 注意

- 既定では前方監視モードなので、`RSTM_MULTI` を変更しない限り既存動作は変わりません。
- 全方位監視モードは、ArduPilot 側で水平面の `RNGFND*` が設定済みであることを前提にしています。
- 停止処理はモード切替で行います。ARM 拒否までは実装していません。
- LiDAR 未接続時に ARM 自体を禁止したい場合は、aux auth を追加してください。
