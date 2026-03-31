--[[
  Rover LiDAR 安全監視

  目的:
    1. LiDAR の距離情報で障害物を判定する
    2. 障害物が近すぎる場合に Rover を停止する
    3. LiDAR 更新が止まった場合に Rover を停止する

  互換性:
    - 既定動作は従来どおり前方 1 台監視のままにする
    - RSTOP_INST の意味も従来どおり維持する
    - RSTM_MULTI=1 にすると、機体の水平面に設定された
      すべてのレンジファインダを監視する
--]]

local PARAM_TABLE_KEY = 118
local PARAM_TABLE_PREFIX = "RSTOP_"
local EXTRA_PARAM_TABLE_KEY = 119
local EXTRA_PARAM_TABLE_PREFIX = "RSTM_"

local MAV_SEVERITY_CRITICAL = 2
local MAV_SEVERITY_INFO = 6

local RNGFND_STATUS_NOT_CONNECTED = 0
local RNGFND_STATUS_NO_DATA = 1
local RNGFND_STATUS_OUT_OF_RANGE_LOW = 2
local RNGFND_STATUS_OUT_OF_RANGE_HIGH = 3
local RNGFND_STATUS_GOOD = 4

-- 全方位監視で対象とする水平面の向き
local HORIZONTAL_ORIENTATION_NAMES = {
    [0] = "front",
    [1] = "front-right",
    [2] = "right",
    [3] = "rear-right",
    [4] = "rear",
    [5] = "rear-left",
    [6] = "left",
    [7] = "front-left",
}

local function bind_param(name)
    local p = Parameter()
    assert(p:init(name), string.format("RSTOP: missing param %s", name))
    return p
end

local function bind_add_param(table_key, table_prefix, name, idx, default_value)
    assert(param:add_param(table_key, idx, name, default_value),
        string.format("RSTOP: add param %s failed", name))
    return bind_param(table_prefix .. name)
end

assert(param:add_table(PARAM_TABLE_KEY, PARAM_TABLE_PREFIX, 9), "RSTOP: add table failed")
assert(param:add_table(EXTRA_PARAM_TABLE_KEY, EXTRA_PARAM_TABLE_PREFIX, 1), "RSTOP: add extra table failed")

local RSTOP_ENABLE = bind_add_param(PARAM_TABLE_KEY, PARAM_TABLE_PREFIX, "ENABLE", 1, 1)
local RSTOP_DIST_M = bind_add_param(PARAM_TABLE_KEY, PARAM_TABLE_PREFIX, "DIST_M", 2, 1.0)
local RSTOP_TOUT_MS = bind_add_param(PARAM_TABLE_KEY, PARAM_TABLE_PREFIX, "TOUT_MS", 3, 500)
local RSTOP_INST = bind_add_param(PARAM_TABLE_KEY, PARAM_TABLE_PREFIX, "INST", 4, 1)
local RSTOP_MODE = bind_add_param(PARAM_TABLE_KEY, PARAM_TABLE_PREFIX, "MODE", 5, 4)
local RSTOP_REQ_ARM = bind_add_param(PARAM_TABLE_KEY, PARAM_TABLE_PREFIX, "REQ_ARM", 6, 1)
local RSTOP_CHECK_MS = bind_add_param(PARAM_TABLE_KEY, PARAM_TABLE_PREFIX, "CHECK_MS", 7, 100)
local RSTOP_MSG_MS = bind_add_param(PARAM_TABLE_KEY, PARAM_TABLE_PREFIX, "MSG_MS", 8, 1000)
local RSTOP_GRACE_MS = bind_add_param(PARAM_TABLE_KEY, PARAM_TABLE_PREFIX, "GRACE_MS", 9, 2000)
local RSTM_MULTI = bind_add_param(EXTRA_PARAM_TABLE_KEY, EXTRA_PARAM_TABLE_PREFIX, "MULTI", 1, 0)

local was_armed = false
local armed_since_ms = 0
local last_fault_key = nil
local last_fault_report_ms = 0

local function now_ms()
    return millis():toint()
end

local function clamp_int(value, min_value, default_value)
    local v = value or default_value
    v = math.floor(v)
    if v < min_value then
        return min_value
    end
    return v
end

local function monitored_instance()
    return clamp_int(RSTOP_INST:get(), 1, 1)
end

local function stop_mode()
    return clamp_int(RSTOP_MODE:get(), 0, 4)
end

local function check_period_ms()
    return clamp_int(RSTOP_CHECK_MS:get(), 50, 100)
end

local function timeout_ms()
    return clamp_int(RSTOP_TOUT_MS:get(), 100, 500)
end

local function report_period_ms()
    return clamp_int(RSTOP_MSG_MS:get(), 200, 1000)
end

local function grace_ms()
    return clamp_int(RSTOP_GRACE_MS:get(), 0, 2000)
end

local function threshold_distance_m()
    local dist = RSTOP_DIST_M:get() or 1.0
    if dist < 0.05 then
        return 0.05
    end
    return dist
end

local function multi_monitor_enabled()
    return RSTM_MULTI:get() > 0
end

local function clear_fault()
    last_fault_key = nil
end

local function send_fault(key, text)
    local now = now_ms()
    if key ~= last_fault_key or (now - last_fault_report_ms) >= report_period_ms() then
        gcs:send_text(MAV_SEVERITY_CRITICAL, text)
        last_fault_key = key
        last_fault_report_ms = now
    end
end

local function stop_vehicle(key, reason_text)
    local mode = stop_mode()
    local current_mode = vehicle:get_mode()

    if current_mode ~= mode then
        if vehicle:set_mode(mode) then
            send_fault(key, string.format("RSTOP: %s -> mode %d", reason_text, mode))
            return
        end

        -- モード変更に失敗した場合は、操舵・スロットルを 0 指令に落とす
        vehicle:set_steering_and_throttle(0.0, 0.0)
        send_fault(key, string.format("RSTOP: %s -> set_mode(%d) failed", reason_text, mode))
        return
    end

    vehicle:set_steering_and_throttle(0.0, 0.0)
    send_fault(key, string.format("RSTOP: %s", reason_text))
end

local function update_arm_state(now)
    local armed = arming:is_armed()

    if armed and not was_armed then
        armed_since_ms = now
    elseif not armed then
        armed_since_ms = 0
        clear_fault()
    end

    was_armed = armed
    return armed
end

local function within_startup_grace(now)
    if armed_since_ms == 0 then
        return false
    end
    return (now - armed_since_ms) < grace_ms()
end

local function orientation_name(orientation)
    return HORIZONTAL_ORIENTATION_NAMES[orientation] or string.format("orient-%d", orientation)
end

local function is_horizontal_orientation(orientation)
    return HORIZONTAL_ORIENTATION_NAMES[orientation] ~= nil
end

local function make_target(instance, backend, label)
    return {
        instance = instance,
        backend = backend,
        label = label,
    }
end

local function target_name(target)
    return string.format("%s[#%d]", target.label, target.instance)
end

-- 従来互換の前方 1 台監視用ターゲットを作成する
local function collect_single_target()
    local instance = monitored_instance()
    local backend = rangefinder:get_backend(instance - 1)
    if not backend then
        return nil
    end
    return { make_target(instance, backend, "front") }
end

-- 全方位監視用に、水平面へ向いたレンジファインダ一覧を作成する
local function collect_horizontal_targets()
    local targets = {}
    local sensor_count = rangefinder:num_sensors()

    for index = 0, sensor_count - 1 do
        local backend = rangefinder:get_backend(index)
        if backend then
            local orientation = backend:orientation()
            if orientation and is_horizontal_orientation(orientation) then
                targets[#targets + 1] = make_target(index + 1, backend, orientation_name(orientation))
            end
        end
    end

    return targets
end

local function collect_targets()
    if multi_monitor_enabled() then
        return collect_horizontal_targets(), "multi"
    end
    return collect_single_target(), "single"
end

-- 監視対象 1 台分を評価し、危険なら停止理由を返す
local function evaluate_target(target, now, startup_grace)
    local backend = target.backend
    local name = target_name(target)

    if not backend then
        if startup_grace then
            return nil
        end
        return {
            key = "missing_" .. target.instance,
            text = string.format("%s not found", name),
        }
    end

    local state = backend:get_state()
    if not state then
        if startup_grace then
            return nil
        end
        return {
            key = "state_" .. target.instance,
            text = string.format("%s state unavailable", name),
        }
    end

    local last_reading = state:last_reading()
    local last_reading_ms = 0
    if last_reading then
        last_reading_ms = last_reading:toint()
    end

    local age_ms = now - last_reading_ms
    if age_ms > timeout_ms() then
        if startup_grace then
            return nil
        end
        return {
            key = "timeout_" .. target.instance,
            text = string.format("%s update timeout %dms > %dms", name, age_ms, timeout_ms()),
        }
    end

    local status = backend:status()

    if status == RNGFND_STATUS_NOT_CONNECTED then
        if startup_grace then
            return nil
        end
        return {
            key = "not_connected_" .. target.instance,
            text = string.format("%s not connected", name),
        }
    end

    if status == RNGFND_STATUS_NO_DATA then
        if startup_grace then
            return nil
        end
        return {
            key = "no_data_" .. target.instance,
            text = string.format("%s has no data", name),
        }
    end

    if status == RNGFND_STATUS_OUT_OF_RANGE_LOW then
        return {
            key = "too_close_" .. target.instance,
            text = string.format("%s obstacle inside sensor minimum range", name),
        }
    end

    if status ~= RNGFND_STATUS_GOOD and status ~= RNGFND_STATUS_OUT_OF_RANGE_HIGH then
        return {
            key = "bad_status_" .. target.instance,
            text = string.format("%s bad status %d", name, status),
        }
    end

    if status == RNGFND_STATUS_GOOD then
        local distance_m = backend:distance()
        local threshold_m = threshold_distance_m()

        if not distance_m then
            if startup_grace then
                return nil
            end
            return {
                key = "invalid_distance_" .. target.instance,
                text = string.format("%s distance unavailable", name),
            }
        end

        if distance_m <= threshold_m then
            return {
                key = "distance_" .. target.instance,
                text = string.format("%s obstacle %.2fm <= %.2fm", name, distance_m, threshold_m),
            }
        end
    end

    return nil
end

local function evaluate_targets(targets, now, startup_grace, monitor_mode)
    if not targets or #targets == 0 then
        if startup_grace then
            return nil
        end

        if monitor_mode == "multi" then
            return {
                key = "no_horizontal_targets",
                text = "No horizontal LiDAR sensors found",
            }
        end

        return {
            key = "missing_single_target",
            text = string.format("front[#%d] not found", monitored_instance()),
        }
    end

    for index = 1, #targets do
        local fault = evaluate_target(targets[index], now, startup_grace)
        if fault then
            return fault
        end
    end

    return nil
end

local function update_impl()
    local now = now_ms()
    local armed = update_arm_state(now)

    if RSTOP_ENABLE:get() <= 0 then
        clear_fault()
        return update, check_period_ms()
    end

    if RSTOP_REQ_ARM:get() > 0 and not armed then
        clear_fault()
        return update, check_period_ms()
    end

    local startup_grace = within_startup_grace(now)
    local targets, monitor_mode = collect_targets()
    local fault = evaluate_targets(targets, now, startup_grace, monitor_mode)

    if fault then
        stop_vehicle(fault.key, fault.text)
        return update, check_period_ms()
    end

    clear_fault()
    return update, check_period_ms()
end

function update()
    local ok, next_fn_or_err, delay_ms = pcall(update_impl)
    if not ok then
        gcs:send_text(MAV_SEVERITY_CRITICAL, "RSTOP: internal error: " .. tostring(next_fn_or_err))
        return update, 1000
    end
    return next_fn_or_err, delay_ms
end

gcs:send_text(MAV_SEVERITY_INFO,
    string.format("RSTOP: loaded inst=%d dist=%.2fm timeout=%dms multi=%d",
        monitored_instance(), threshold_distance_m(), timeout_ms(), clamp_int(RSTM_MULTI:get(), 0, 0)))

return update()
