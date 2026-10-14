#!/usr/bin/env bash
set -eo pipefail

project_root="$(
  cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd
)"
sdk_root="${project_root}/unitree_sdk2-2.0.2"

unset AMENT_PREFIX_PATH
unset CMAKE_PREFIX_PATH
unset COLCON_PREFIX_PATH
unset PYTHONPATH
unset RMW_IMPLEMENTATION
unset CYCLONEDDS_URI
unset CONDA_PREFIX
unset CONDA_DEFAULT_ENV
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

robot_nic="$(
  ip -o -4 address show up | awk \
    '$4 ~ /^192[.]168[.]123[.]/ {print $2; exit}'
)"
if [[ -z "$robot_nic" ]]; then
  echo "没有找到配置了 192.168.123.* 地址的 Go2 网卡" >&2
  ip -br address >&2
  exit 1
fi

cd "$sdk_root"
if [[ ! -x build/bin/go2_camera_capture ]]; then
  echo "找不到 build/bin/go2_camera_capture" >&2
  exit 1
fi

echo "Go2 network interface: $robot_nic"
export LD_LIBRARY_PATH="${sdk_root}/thirdparty/lib/x86_64:/usr/lib/x86_64-linux-gnu"
exec ./build/bin/go2_camera_capture "$robot_nic"
