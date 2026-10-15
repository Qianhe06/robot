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

echo "===== 保持运动禁用 ====="
timeout 8 ros2 service call /go2_motion/enable \
  std_srvs/srv/SetBool '{data: false}'

echo "===== 单帧激光净空诊断 ====="
/usr/bin/python3 "${project_root}/scripts/diagnose_scan_clearance.py"

echo "诊断完成；未发送运动指令。"
