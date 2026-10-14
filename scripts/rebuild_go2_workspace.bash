#!/usr/bin/env bash
set -euo pipefail

project_root="$(
  cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd
)"
workspace="${project_root}/ros2_ws"

unset CONDA_PREFIX
unset CONDA_DEFAULT_ENV
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

unset AMENT_PREFIX_PATH
unset CMAKE_PREFIX_PATH
unset COLCON_PREFIX_PATH
unset PYTHONPATH

source "${project_root}/activate_go2.bash"

echo "检查 Unitree ROS 2 Python 消息……"
if ! /usr/bin/python3 -c "from unitree_api.msg import Request"; then
  echo "项目内 Unitree 安装不完整，开始重建……"
  bash "${project_root}/scripts/rebuild_unitree_ros2.bash"
  unset AMENT_PREFIX_PATH
  unset CMAKE_PREFIX_PATH
  unset COLCON_PREFIX_PATH
  unset PYTHONPATH
  source "${project_root}/activate_go2.bash"
fi
/usr/bin/python3 -c \
  "from unitree_api.msg import Request; print('unitree_api 正常')"

cd "$workspace"
rm -rf build/go2_scissors_search install/go2_scissors_search

echo "使用系统 Python 重新构建 go2_scissors_search……"
/usr/bin/colcon build --packages-select go2_scissors_search

unset AMENT_PREFIX_PATH
unset CMAKE_PREFIX_PATH
unset COLCON_PREFIX_PATH
unset PYTHONPATH
source "${project_root}/activate_go2.bash"

echo "检查 Python 包元数据……"
/usr/bin/python3 -c \
  "from importlib.metadata import version; print('go2_scissors_search', version('go2-scissors-search'))"

echo "检查 ROS 2 可执行程序……"
ros2 pkg executables go2_scissors_search

echo
echo "重建成功。新终端请执行："
echo "source \"${project_root}/activate_go2.bash\""
