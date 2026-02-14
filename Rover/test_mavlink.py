from pymavlink import mavutil

conn = mavutil.mavlink_connection("udpin:0.0.0.0:14550")
conn.wait_heartbeat(timeout=10)
print("Heartbeat OK:", conn.target_system, conn.target_component)

msg = conn.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=10)
print("GLOBAL_POSITION_INT:", msg.to_dict() if msg else "timeout")
