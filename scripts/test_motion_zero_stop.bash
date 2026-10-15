#!/usr/bin/env bash
set -eo pipefail

project_root="$(
  cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd
)"

if [[ "${1:-}" != "--robot-secured" ]]; then
  echo "请先清空机器人周围区域、握住遥控器并准备物理急停。" >&2
  echo "准备完成后使用参数 --robot-secured 重新运行。" >&2
  exit 2
fi

unset AMENT_PREFIX_PATH
unset CMAKE_PREFIX_PATH
unset COLCON_PREFIX_PATH
unset PYTHONPATH
source "${project_root}/activate_go2.bash"

zero_publisher_pid=""

cleanup() {
  trap - EXIT
  set +e
  if [[ -n "$zero_publisher_pid" ]]; then
    kill "$zero_publisher_pid" 2>/dev/null
    wait "$zero_publisher_pid" 2>/dev/null
  fi
  timeout 5 ros2 service call /go2_motion/emergency_stop \
    std_srvs/srv/Trigger '{}' >/dev/null 2>&1
  timeout 5 ros2 service call /go2_motion/enable \
    std_srvs/srv/SetBool '{data: false}' >/dev/null 2>&1
}
trap cleanup EXIT
trap 'exit 130' INT TERM

echo "===== 取消自主任务 ====="
timeout 8 ros2 service call /scissors_search/enable \
  std_srvs/srv/SetBool '{data: false}'

echo "===== 发布连续零速度 ====="
ros2 topic pub --rate 20 /cmd_vel geometry_msgs/msg/Twist '{}' \
  >/dev/null 2>&1 &
zero_publisher_pid=$!
sleep 1

echo "===== 启用运动接口（速度保持为零） ====="
enable_response="$(
  timeout 8 ros2 service call /go2_motion/enable \
    std_srvs/srv/SetBool '{data: true}' 2>&1
)"
printf '%s\n' "$enable_response"
if ! grep -q 'success=True' <<<"$enable_response"; then
  echo "运动接口拒绝启用；立即执行安全清理。" >&2
  exit 1
fi

echo "连续发送零速度 2 秒……"
sleep 2

echo "===== 调用急停 ====="
stop_response="$(
  timeout 8 ros2 service call /go2_motion/emergency_stop \
    std_srvs/srv/Trigger '{}' 2>&1
)"
printf '%s\n' "$stop_response"
if ! grep -q 'success=True' <<<"$stop_response"; then
  echo "急停服务没有返回成功；立即执行安全清理。" >&2
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

echo "零速度、启用、急停和 StopMove 验证通过；运动保持禁用。"
