#!/usr/bin/env bash
set -eo pipefail

project_root="$(
  cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd
)"

conda_executable="$(command -v conda || true)"
if [[ -z "$conda_executable" ]]; then
  echo "找不到 conda，无法激活 robot 环境" >&2
  exit 1
fi
eval "$("$conda_executable" shell.bash hook)"
conda activate robot

unset AMENT_PREFIX_PATH
unset CMAKE_PREFIX_PATH
unset COLCON_PREFIX_PATH
unset PYTHONPATH
source "${project_root}/activate_go2.bash"

for topic in \
  /camera/image_raw \
  /camera/camera_info \
  /utlidar/cloud_base \
  /utlidar/robot_odom; do
  echo "检查 $topic……"
  if ! timeout 8 ros2 topic echo "$topic" --once >/dev/null; then
    echo "$topic 没有数据，未启动自主系统" >&2
    exit 1
  fi
done

exec ros2 launch go2_scissors_search autonomy.launch.py
