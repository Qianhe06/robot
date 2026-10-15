#!/usr/bin/env bash
set -eo pipefail

project_root="$(
  cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd
)"

speed="0.05"
duration="0.8"
expected_distance="0.04"
clearance="1"
speed_pattern='x: +0[.]0?5([0]*)([[:space:]]|$)'

case "${2:-}" in
  "")
    ;;
  --nav-calibration)
    speed="0.1"
    duration="1.5"
    expected_distance="0.15"
    clearance="1.5"
    speed_pattern='x: +0[.]10*([[:space:]]|$)'
    ;;
  *)
    echo "未知测试档：${2}" >&2
    exit 2
    ;;
esac

if [[ "${1:-}" != "--clear-path-confirmed" ]]; then
  echo "该测试会让机器人以 ${speed} m/s 向前运动 ${duration} 秒。" >&2
  echo "预计前进约 ${expected_distance} 米。" >&2
  echo "请清空前方至少 ${clearance} 米区域，握住遥控器并准备物理急停。" >&2
  echo "准备完成后使用参数 --clear-path-confirmed 重新运行。" >&2
  exit 2
fi

unset AMENT_PREFIX_PATH
unset CMAKE_PREFIX_PATH
unset COLCON_PREFIX_PATH
unset PYTHONPATH
source "${project_root}/activate_go2.bash"

command_publisher_pid=""

cleanup() {
  trap - EXIT
  set +e
  timeout 5 ros2 service call /go2_motion/emergency_stop \
    std_srvs/srv/Trigger '{}' >/dev/null 2>&1
  timeout 5 ros2 service call /go2_motion/enable \
    std_srvs/srv/SetBool '{data: false}' >/dev/null 2>&1
  if [[ -n "$command_publisher_pid" ]]; then
    kill "$command_publisher_pid" 2>/dev/null
    wait "$command_publisher_pid" 2>/dev/null
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

echo "===== 取消任务并禁用运动 ====="
timeout 8 ros2 service call /scissors_search/enable \
  std_srvs/srv/SetBool '{data: false}'
timeout 8 ros2 service call /go2_motion/enable \
  std_srvs/srv/SetBool '{data: false}'

echo "===== 检查速度话题链路 ====="
nav_info="$(ros2 topic info /cmd_vel_nav 2>&1 || true)"
output_info="$(ros2 topic info /cmd_vel 2>&1 || true)"
echo "--- /cmd_vel_nav ---"
echo "$nav_info"
echo "--- /cmd_vel ---"
echo "$output_info"
if ! grep -Eq 'Subscription count: [1-9][0-9]*' <<<"$nav_info"; then
  echo "/cmd_vel_nav 没有订阅者，测试终止。" >&2
  exit 1
fi
if ! grep -Eq 'Subscription count: [1-9][0-9]*' <<<"$output_info"; then
  echo "/cmd_vel 没有订阅者，测试终止。" >&2
  exit 1
fi

echo "===== 准备 ${speed} m/s 指令（运动仍禁用） ====="
ros2 topic pub --rate 20 /cmd_vel_nav geometry_msgs/msg/Twist \
  "{linear: {x: ${speed}, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}" \
  >/dev/null 2>&1 &
command_publisher_pid=$!
sleep 2

effective_command="$(
  timeout 5 ros2 topic echo /cmd_vel --once 2>&1 || true
)"
printf '%s\n' "$effective_command"
if ! grep -Eq "$speed_pattern" <<<"$effective_command"; then
  echo "没有在 /cmd_vel 观察到预期的 ${speed} m/s 指令。" >&2
  exit 1
fi

echo "===== 测试前 SLAM 位姿 ====="
pose_before="$(
  timeout 3 ros2 run tf2_ros tf2_echo map base_footprint 2>&1 || true
)"
echo "$pose_before" | tail -20
if ! grep -q 'Translation:' <<<"$pose_before"; then
  echo "测试前无法读取 map -> base_footprint。" >&2
  exit 1
fi

echo "===== 启用运动 ${duration} 秒 ====="
enable_response="$(
  timeout 8 ros2 service call /go2_motion/enable \
    std_srvs/srv/SetBool '{data: true}' 2>&1
)"
printf '%s\n' "$enable_response"
if ! grep -q 'success=True' <<<"$enable_response"; then
  echo "运动接口拒绝启用；立即执行安全清理。" >&2
  exit 1
fi
sleep "$duration"

echo "===== 立即急停 ====="
stop_response="$(
  timeout 8 ros2 service call /go2_motion/emergency_stop \
    std_srvs/srv/Trigger '{}' 2>&1
)"
printf '%s\n' "$stop_response"
if ! grep -q 'success=True' <<<"$stop_response"; then
  echo "急停服务没有返回成功；立即执行安全清理。" >&2
  exit 1
fi

kill "$command_publisher_pid" 2>/dev/null || true
wait "$command_publisher_pid" 2>/dev/null || true
command_publisher_pid=""

echo "等待 SLAM 校正 3 秒……"
sleep 3
echo "===== 测试后 SLAM 位姿 ====="
pose_after="$(
  timeout 3 ros2 run tf2_ros tf2_echo map base_footprint 2>&1 || true
)"
echo "$pose_after" | tail -20
if ! grep -q 'Translation:' <<<"$pose_after"; then
  echo "测试后无法读取 map -> base_footprint。" >&2
  exit 1
fi

echo "===== 验证 StopMove 请求 ====="
sport_request="$(
  timeout 8 ros2 topic echo /api/sport/request --once \
    --filter 'm.header.identity.api_id == 1003' 2>&1 || true
)"
printf '%s\n' "$sport_request"
if ! grep -Eq 'api_id: +1003([[:space:]]|$)' <<<"$sport_request"; then
  echo "没有观察到 Unitree StopMove（API 1003）请求。" >&2
  exit 1
fi

echo "===== 明确禁用运动 ====="
timeout 8 ros2 service call /go2_motion/enable \
  std_srvs/srv/SetBool '{data: false}'

echo "低速前进测试完成；预计位移 ${expected_distance} 米，运动保持禁用。"
