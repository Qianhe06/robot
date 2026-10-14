#!/usr/bin/env bash
set -eo pipefail

project_root="$(
  cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd
)"
unitree_workspace="${project_root}/unitree_ros2/cyclonedds_ws"
temporary_root="$(mktemp -d /tmp/go2-unitree-build.XXXXXX)"
temporary_workspace="${temporary_root}/cyclonedds_ws"

cleanup() {
  rm -rf "$temporary_root"
}
trap cleanup EXIT

if [[ ! -d "${unitree_workspace}/src" ]]; then
  echo "找不到 ${unitree_workspace}/src" >&2
  exit 1
fi

echo "复制 Unitree ROS 2 源码到临时 ASCII 路径……"
mkdir -p "$temporary_workspace"
cp -a "${unitree_workspace}/src" "${temporary_workspace}/src"

unset AMENT_PREFIX_PATH
unset CMAKE_PREFIX_PATH
unset COLCON_PREFIX_PATH
unset PYTHONPATH
unset CONDA_PREFIX
unset CONDA_DEFAULT_ENV
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

source /opt/ros/humble/setup.bash

echo "构建 unitree_api 和 unitree_go……"
cd "$temporary_workspace"
/usr/bin/colcon build \
  --packages-select unitree_api unitree_go \
  --cmake-args \
  -DPython3_EXECUTABLE=/usr/bin/python3 \
  -DPYTHON_EXECUTABLE=/usr/bin/python3

source "${temporary_workspace}/install/setup.bash"
/usr/bin/python3 -c \
  "from unitree_api.msg import Request; print('临时构建验证正常')"

echo "将 Unitree 安装结果复制回项目目录……"
rm -rf "${unitree_workspace}/build"
rm -rf "${unitree_workspace}/install"
rm -rf "${unitree_workspace}/log"
cp -a "${temporary_workspace}/install" "${unitree_workspace}/install"

unset AMENT_PREFIX_PATH
unset CMAKE_PREFIX_PATH
unset COLCON_PREFIX_PATH
unset PYTHONPATH
source "${project_root}/activate_go2.bash"
/usr/bin/python3 -c \
  "from unitree_api.msg import Request; print('项目内 unitree_api 正常')"

echo "Unitree ROS 2 重建成功。"
