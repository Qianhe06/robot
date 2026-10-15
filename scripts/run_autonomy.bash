#!/usr/bin/env bash
set -eo pipefail

project_root="$(
  cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd
)"
log_dir="${project_root}/logs/runtime"
mkdir -p "$log_dir"
log_file="${log_dir}/autonomy_$(date +%Y%m%d_%H%M%S)_$$.log"
exec > >(tee -a "$log_file") 2>&1
echo "运行日志：$log_file"
export PYTHONUNBUFFERED=1
export ROS_LOG_DIR="${log_dir}/ros"

model_path="${project_root}/yolo26x.pt"

if [[ ! -s "$model_path" ]]; then
  echo "YOLO 模型不存在或为空：$model_path" >&2
  exit 1
fi
if head -n 1 "$model_path" | grep -q \
  '^version https://git-lfs.github.com/spec/v1'; then
  echo "yolo26x.pt 仍是 Git LFS 指针，请先执行：" >&2
  echo "git -C \"${project_root}\" lfs pull --include=yolo26x.pt" >&2
  exit 1
fi

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
