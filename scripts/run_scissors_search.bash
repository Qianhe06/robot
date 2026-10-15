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

stop_search() {
  trap - EXIT INT TERM
  set +e
  timeout 5 ros2 service call /scissors_search/enable \
    std_srvs/srv/SetBool '{data: false}' >/dev/null 2>&1
  timeout 5 ros2 service call /go2_motion/enable \
    std_srvs/srv/SetBool '{data: false}' >/dev/null 2>&1
  echo "搜索已取消，运动已禁用。"
}
trap stop_search EXIT INT TERM

echo "===== 启动剪刀搜索 ====="
response="$(timeout 8 ros2 service call /scissors_search/enable \
  std_srvs/srv/SetBool '{data: true}')"
printf '%s\n' "$response"
if ! grep -q 'success=True' <<<"$response"; then
  echo "搜索启动失败。" >&2
  exit 1
fi

echo "===== 任务状态（按 Ctrl+C 取消并禁用运动） ====="
ros2 topic echo /scissors_search/status \
  --qos-durability transient_local
