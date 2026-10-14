#!/usr/bin/env bash
# Source this file to activate the ROS 2 environment for this checkout.

_go2_project_root="$(
  cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd
)"
_go2_unitree_install="${_go2_project_root}/unitree_ros2/cyclonedds_ws/install"
_go2_package_prefix="${_go2_project_root}/ros2_ws/install/go2_scissors_search"

if [[ ! -f /opt/ros/humble/setup.bash ]]; then
  echo "找不到 /opt/ros/humble/setup.bash" >&2
  return 1
fi

source /opt/ros/humble/setup.bash

_go2_prepend() {
  local variable_name="$1"
  local path_value="$2"
  [[ -d "$path_value" ]] || return 0
  local current_value="${!variable_name-}"
  if [[ ":${current_value}:" != *":${path_value}:"* ]]; then
    printf -v "$variable_name" '%s' \
      "${path_value}${current_value:+:${current_value}}"
    export "$variable_name"
  fi
}

for _go2_prefix in "${_go2_unitree_install}"/unitree_*; do
  [[ -d "$_go2_prefix" ]] || continue
  _go2_prepend AMENT_PREFIX_PATH "$_go2_prefix"
  _go2_prepend CMAKE_PREFIX_PATH "$_go2_prefix"
  _go2_prepend LD_LIBRARY_PATH "${_go2_prefix}/lib"
done

if [[ -d "$_go2_unitree_install" ]]; then
  while IFS= read -r _go2_python_path; do
    _go2_prepend PYTHONPATH "$_go2_python_path"
  done < <(
    find "$_go2_unitree_install" -type d \
      \( -name site-packages -o -name dist-packages \) -print
  )
fi

_go2_prepend AMENT_PREFIX_PATH "$_go2_package_prefix"
_go2_prepend PATH "${_go2_package_prefix}/lib/go2_scissors_search"

if [[ -d "$_go2_package_prefix" ]]; then
  while IFS= read -r _go2_python_path; do
    _go2_prepend PYTHONPATH "$_go2_python_path"
  done < <(
    find "$_go2_package_prefix" -type d \
      \( -name site-packages -o -name dist-packages \) -print
  )
fi

_go2_prepend PYTHONPATH \
  "${_go2_project_root}/ros2_ws/src/go2_scissors_search"

export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp

_go2_robot_nic="$(
  ip -o -4 address show up 2>/dev/null | awk \
    '$4 ~ /^192[.]168[.]123[.]/ {print $2; exit}'
)"
if [[ -n "$_go2_robot_nic" ]]; then
  export GO2_NETWORK_INTERFACE="$_go2_robot_nic"
  export CYCLONEDDS_URI="<CycloneDDS><Domain><General><Interfaces><NetworkInterface name=\"${_go2_robot_nic}\" priority=\"default\" multicast=\"default\" /></Interfaces></General></Domain></CycloneDDS>"
else
  unset GO2_NETWORK_INTERFACE
  echo "警告：没有找到配置了 192.168.123.* 地址的 Go2 网卡" >&2
fi

unset _go2_project_root
unset _go2_unitree_install
unset _go2_package_prefix
unset _go2_prefix
unset _go2_python_path
unset _go2_robot_nic
unset -f _go2_prepend
