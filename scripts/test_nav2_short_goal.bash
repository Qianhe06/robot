#!/usr/bin/env bash
set -eo pipefail

project_root="$(
  cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd
)"

if [[ "${1:-}" != "--clear-path-confirmed" ]]; then
  echo "该测试会让机器人通过 Nav2 向正前方导航 0.25 米。" >&2
  echo "请使用平整防滑地面，清空正前方至少 0.8 米。" >&2
  echo "请握住遥控器并准备物理急停。" >&2
  echo "准备完成后使用参数 --clear-path-confirmed 重新运行。" >&2
  exit 2
fi

unset AMENT_PREFIX_PATH
unset CMAKE_PREFIX_PATH
unset COLCON_PREFIX_PATH
unset PYTHONPATH
source "${project_root}/activate_go2.bash"

cleanup() {
  trap - EXIT
  set +e
  timeout 5 ros2 service call /go2_motion/emergency_stop \
    std_srvs/srv/Trigger '{}' >/dev/null 2>&1
  timeout 5 ros2 service call /go2_motion/enable \
    std_srvs/srv/SetBool '{data: false}' >/dev/null 2>&1

}
trap cleanup EXIT
trap 'exit 130' INT TERM

echo "===== 取消自主任务 ====="
timeout 8 ros2 service call /scissors_search/enable \
  std_srvs/srv/SetBool '{data: false}'

echo "===== 重新执行运动前检查 ====="
bash "${project_root}/scripts/check_motion_preflight.bash"

echo "===== 验证控制器容差一致 ====="
for parameter in goal_checker.xy_goal_tolerance FollowPath.xy_goal_tolerance; do
  value="$(ros2 param get /controller_server "$parameter" | awk '{print $NF}')"
  if [[ "$value" != "0.04" ]]; then
    echo "$parameter=$value；请重新构建并重启自主系统，加载统一的 0.04 米容差。" >&2
    exit 1
  fi
done

echo "===== Nav2 正前方 0.25 米目标测试 ====="
/usr/bin/python3 "${project_root}/scripts/nav2_short_goal_test.py" \
  --distance 0.25 \
  --timeout 25

echo "Nav2 短距离测试完成；运动保持禁用。"
