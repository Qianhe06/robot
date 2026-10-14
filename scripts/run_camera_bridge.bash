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

camera_info_pid=""
cleanup() {
  if [[ -n "$camera_info_pid" ]]; then
    kill "$camera_info_pid" 2>/dev/null || true
    wait "$camera_info_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

/usr/bin/python3 "${project_root}/go2_camera_info_publisher.py" &
camera_info_pid=$!
/usr/bin/python3 "${project_root}/go2_camera_ros2_bridge.py"
