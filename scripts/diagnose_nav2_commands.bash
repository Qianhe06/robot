#!/usr/bin/env bash
set -eo pipefail

project_root="$(
  cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd
)"

unset AMENT_PREFIX_PATH
unset CMAKE_PREFIX_PATH
unset COLCON_PREFIX_PATH
unset PYTHONPATH
source "${project_root}/activate_go2.bash"

cleanup() {
  trap - EXIT
  set +e
  timeout 5 ros2 service call /go2_motion/emergency_stop \
    std_srvs/srv/Trigger '{}' >/dev/null 2>&1
  timeout 5 ros2 service call /go2_motion/enable \
    std_srvs/srv/SetBool '{data: false}' >/dev/null 2>&1
}
trap cleanup EXIT
trap 'exit 130' INT TERM

echo "===== 取消任务并保持运动禁用 ====="
timeout 8 ros2 service call /scissors_search/enable \
  std_srvs/srv/SetBool '{data: false}'
timeout 8 ros2 service call /go2_motion/enable \
  std_srvs/srv/SetBool '{data: false}'

echo "===== Nav2 命令干运行（机器人不会移动） ====="
/usr/bin/python3 "${project_root}/scripts/nav2_short_goal_test.py" \
  --distance 0.25 \
  --timeout 25 \
  --dry-run-seconds 4

echo "诊断完成；运动保持禁用。"
