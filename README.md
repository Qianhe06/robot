# Go2 自主寻找墙上剪刀

本项目在 Unitree Go2 上运行 ROS 2 Humble、SLAM Toolbox 和 Nav2。任务启动后先原地旋转一圈补充 2D 地图，再导航到可到达的未建图区域边缘。地图没有开放的前沿后，机器人沿已建地图中的墙面生成观察位姿巡检；确认墙上剪刀后，在近距离用 Go2 原生避障接口缓慢接近。实时 YOLO 剪刀检测置信度超过 90% 时停车并报告找到目标。未找到目标时返回任务起点。

## 环境与构建

推荐使用 Ubuntu 22.04、ROS 2 Humble、CUDA 和已验证可运行的 YOLO 环境。Unitree 消息包必须先按 `unitree_ros2` 的说明构建。

```bash
cd ~/桌面/robot/detection
git submodule update --init --recursive
source /opt/ros/humble/setup.bash

cd unitree_ros2/cyclonedds_ws
colcon build --packages-select unitree_api unitree_go
source install/setup.bash

cd ../../ros2_ws
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install --packages-select go2_scissors_search
source install/setup.bash
```

如果项目不在 `~/桌面/robot/detection`，只需把第一条 `cd` 改成实际路径。`autonomy.launch.py` 会从工作空间上级目录查找 `go2_scissors_rgb_lidar.py`。

## 启动

先连接 Go2 网络并确认这些话题有持续数据：

```bash
ros2 topic hz /utlidar/cloud_base
ros2 topic hz /utlidar/robot_odom
ros2 topic hz /camera/image_raw
ros2 topic echo /camera/camera_info --once
```

启动完整系统：

```bash
source /opt/ros/humble/setup.bash
source ~/桌面/robot/detection/unitree_ros2/cyclonedds_ws/install/setup.bash
source ~/桌面/robot/detection/ros2_ws/install/setup.bash
ros2 launch go2_scissors_search autonomy.launch.py
```

系统启动后仍保持禁止运动。地图、相机、点云、里程计和 Unitree 接口都正常时，开始任务：

```bash
ros2 service call /scissors_search/enable std_srvs/srv/SetBool "{data: true}"
```

启动任务会调用 Nav2 的 Spin 动作原地转 360°，成功后才开始前沿探索；Spin 失败会停止任务。地图仍有未知边界、但没有新的安全前沿目标时，状态会报告 `map remains open` 并等待地图变化，不会误报建图完成。只有已建自由区域不再接壤未知区域时，才转入墙面巡检。受遮挡或不可到达的区域可能无法仅靠自主行走形成封闭地图。

## RViz 地图与目标

保持自主系统运行，在另一个终端打开项目提供的 RViz 配置：

```bash
PROJECT="$HOME/桌面/robot/detection"
source "$PROJECT/activate_go2.bash"
rviz2 -d "$PROJECT/ros2_ws/src/go2_scissors_search/config/scissors_mapping.rviz"
```

固定坐标系为 `map`。灰色/黑色区域显示 `/map` 的 2D 地图；`Go2 position` 的坐标轴来自实时 `map → base_footprint` TF；`Scissors position` 用红点显示 `/scissors/marker` 的最近一次剪刀候选位置。候选停止更新 1 秒后红点消失；经过多帧确认的地图坐标仍可在 `/scissors/target_pose` 查看。可在 RViz 的 Displays 面板查看各显示项状态。

任务管理器会显式调用 `/go2_motion/enable`。运动适配器通过 Go2 官方
`/api/obstacles_avoid/request` 接口发送 API 1004 选择 API 指令源、API 1001
开启原生避障，并以 20 Hz 将选定的速度指令转成 API 1003
`Move(mode=0)` 请求。通常使用 Nav2 的 `/cmd_vel`；近距离接近时切换到
`/cmd_vel_target`，不混用两路指令。已确认目标位于机器人前方、墙面自由侧且距离
不超过 1.25 m 时才接近，不再搜索地图停车位。接近速度不超过 `0.08 m/s`；实时
YOLO 剪刀置信度超过 90% 时停车。中心距目标达到 `0.62 m`、原生避障阻止前进或
接近超时而置信度仍不足 90% 时停车并报告失败。YOLO 置信度不保证随距离增加，
不能用它估计物理距离。普通导航仍拒绝全局代价为 253–255 的目标；通用线速度限制
为 `0.25 m/s`，角速度限制为 `0.40 rad/s`。

LiDAR 转换的 `/scan_raw` 会过滤位于 `0.76 × 0.34 m` 机身轮廓内的回波，再作为
`/scan` 交给 SLAM 和 Nav2；Go2 自身的原生避障接口保持启用。

停止任务或急停：

```bash
ros2 service call /scissors_search/enable std_srvs/srv/SetBool "{data: false}"
ros2 service call /go2_motion/emergency_stop std_srvs/srv/Trigger "{}"
```

急停或 watchdog 故障会锁定运动，并同时发送零速度和 `StopMove`。排除故障后必须重新开始任务或显式调用 `/go2_motion/enable`，机器人不会自行恢复运动。

## 状态与调试

```bash
ros2 topic echo /scissors_search/status
ros2 topic echo /scissors/candidate
ros2 topic echo /scissors/detection_confidence
ros2 topic echo /scissors/target_pose
ros2 topic echo /scissors_search/inspection_path
ros2 action list
```

任务状态依次包括 `MAPPING`、`EXPLORING`、`INSPECTING`、`CONFIRMING`、`APPROACHING`，最终进入 `FOUND` 或返回起点后进入 `NOT_FOUND`。故障和人工取消分别报告 `FAULT`、`CANCELLED`。

检测定位使用时间戳匹配的单帧 RGB、点云和 odom。YOLO 框周围的点用于拟合竖直墙面，框中心射线与墙面求交得到目标位置；最近 5 帧至少 3 帧有效、墙面点不少于 20、平面 RMS 不超过 5 cm、位置离散不超过 25 cm 后才发布 `/scissors/target_pose`。

## 首次实机验证

首次运行按以下顺序进行，每一步通过后再进入下一步：

1. 将机器人架空或保持原地，验证启用、速度限幅、零速度、`StopMove` 和急停。
2. 在围栏小区域验证地图、Nav2、避障、观察点转向扫描和返回起点。
3. 在三个不同墙面位置分别验证剪刀定位、实时置信度和最小墙面间距。
4. 放置两把剪刀，确认近距离接近不会切换到错误目标。
5. 分别断开相机、LiDAR 和里程计数据，确认 watchdog 在设定超时后停车并锁定。

首次实机测试时应携带遥控器并保持物理急停能力。实测机身约为
`0.70 × 0.28 m`，测量误差不超过 `0.03 m`；当前 Nav2 footprint 按余量设为
`0.76 × 0.34 m`。探索目标的地图足迹额外留 `0.05 m`，Nav2 代价地图
仍使用 `0.15 m` 障碍膨胀。若实测尺寸更大，应修改
`config/nav2.yaml` 和任务管理器参数，增大机身范围。

## 测试

纯算法测试不依赖正在运行的 ROS 图：

```bash
cd ~/桌面/robot/detection/ros2_ws/src/go2_scissors_search
python3 -m pytest -q
```

测试覆盖射线与墙面求交、竖直墙面 RANSAC、时序确认、目标聚类、前沿和墙面观察位姿、安全 footprint、Unitree JSON、限速和全部 watchdog。移动状态下的时间同步稳定性仍需使用实机录包回放验证。
