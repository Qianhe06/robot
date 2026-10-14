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

echo "===== 节点 ====="
ros2 node list | sort

echo "===== 项目服务 ====="
services="$(ros2 service list | grep -E 'go2_motion|scissors_search' || true)"
printf '%s\n' "$services"

if grep -qx '/go2_motion/enable' <<<"$services"; then
  echo "===== 明确禁用运动 ====="
  ros2 service call /go2_motion/enable \
    std_srvs/srv/SetBool '{data: false}'
fi

echo "===== 任务状态 ====="
timeout 10 ros2 topic echo /scissors_search/status --once \
  --qos-durability transient_local || true

echo "===== 关键话题 ====="
for topic in \
  /camera/image_raw \
  /camera/camera_info \
  /utlidar/cloud_base \
  /utlidar/robot_odom \
  /sensors/cloud_base_synced \
  /sensors/odom_synced \
  /scan; do
  echo "--- $topic ---"
  ros2 topic info "$topic"
done
