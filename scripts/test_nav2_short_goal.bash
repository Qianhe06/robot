#!/usr/bin/env bash
set -eo pipefail

project_root="$(
  cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd
)"

if [[ "${1:-}" != "--clear-path-confirmed" ]]; then
  echo "该测试会让机器人通过 Nav2 向前导航 0.50 米。" >&2
  echo "请使用平整防滑地面，清空前方 90 度扇区至少 2 米。" >&2
  echo "请握住遥控器并准备物理急停。" >&2
  echo "准备完成后使用参数 --clear-path-confirmed 重新运行。" >&2
  exit 2
fi

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

echo "===== 取消自主任务 ====="
timeout 8 ros2 service call /scissors_search/enable \
  std_srvs/srv/SetBool '{data: false}'

echo "===== 重新执行运动前检查 ====="
bash "${project_root}/scripts/check_motion_preflight.bash"

echo "===== Nav2 前方 0.50 米目标测试 ====="
/usr/bin/python3 "${project_root}/scripts/nav2_short_goal_test.py" \
  --distance 0.50 \
  --timeout 25

echo "Nav2 短距离测试完成；运动保持禁用。"
