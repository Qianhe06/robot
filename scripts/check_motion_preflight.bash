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

failures=0

fail() {
  echo "失败：$1" >&2
  failures=$((failures + 1))
}

echo "===== 保持运动禁用 ====="
if ! timeout 8 ros2 service call /go2_motion/enable \
  std_srvs/srv/SetBool '{data: false}'; then
  fail "无法调用 /go2_motion/enable"
fi

echo "===== Nav2 生命周期 ====="
for node in \
  /controller_server \
  /planner_server \
  /bt_navigator \
  /behavior_server \
  /waypoint_follower \
  /velocity_smoother; do
  state="$(timeout 5 ros2 lifecycle get "$node" 2>&1 || true)"
  echo "$node: $state"
  grep -q 'active' <<<"$state" || fail "$node 不是 active"
done

echo "===== 地图与代价地图 ====="
for topic in \
  /map \
  /local_costmap/costmap_raw \
  /global_costmap/costmap_raw; do
  info="$(ros2 topic info "$topic" 2>&1 || true)"
  echo "--- $topic ---"
  echo "$info"
  grep -Eq 'Publisher count: [1-9][0-9]*' <<<"$info" || \
    fail "$topic 没有发布者"
done

if ! timeout 8 ros2 topic echo /map --once \
  --qos-durability transient_local >/dev/null; then
  fail "/map 没有收到地图数据"
else
  echo "/map 已收到地图数据"
fi

echo "===== map 到机器人 TF ====="
tf_output="$(
  timeout 5 ros2 run tf2_ros tf2_echo map base_footprint 2>&1 || true
)"
echo "$tf_output" | tail -20
grep -q 'Translation:' <<<"$tf_output" || \
  fail "map -> base_footprint TF 不可用"

echo "===== Nav2 action ====="
actions="$(ros2 action list 2>&1 || true)"
printf '%s\n' "$actions"
grep -qx '/navigate_to_pose' <<<"$actions" || \
  fail "/navigate_to_pose 不可用"
grep -qx '/compute_path_to_pose' <<<"$actions" || \
  fail "/compute_path_to_pose 不可用"

echo "===== Unitree 避障接口响应 ====="
obstacle_response="$(
  timeout 8 ros2 topic echo /api/obstacles_avoid/response --once 2>&1 || true
)"
printf '%s\n' "$obstacle_response"
if ! grep -Eq 'code: +0([[:space:]]|$)' <<<"$obstacle_response"; then
  fail "未收到 Unitree 避障接口的成功响应"
fi

echo "===== 运动前检查结果 ====="
if ((failures > 0)); then
  echo "共有 $failures 项失败；运动保持禁用。" >&2
  exit 1
fi

echo "全部通过；运动仍保持禁用。"
