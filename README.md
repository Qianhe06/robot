# Go2 自主寻找墙上剪刀

本项目在 Unitree Go2 上运行 ROS 2 Humble、SLAM Toolbox 和 Nav2。机器人先执行前沿探索，再沿已建地图中的墙面生成观察位姿巡检；检测节点确认墙上剪刀后，任务管理器按 Nav2 路径长度选择最近的目标，导航到安全停车位并面向目标停止。未找到目标时返回任务起点。

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

任务管理器会显式调用 `/go2_motion/enable`。运动适配器仅在全部数据流新鲜时接受启用，并以 20 Hz 将 Nav2 的 `/cmd_vel` 转成 Unitree 避障 `Move(mode=0)` 请求。线速度限制为 `0.25 m/s`，角速度限制为 `0.40 rad/s`。

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
3. 在三个不同墙面位置分别验证剪刀定位、停车位和最终朝向。
4. 放置两把剪刀，确认系统选择 Nav2 路径较短且有有效路径的目标。
5. 分别断开相机、LiDAR 和里程计数据，确认 watchdog 在设定超时后停车并锁定。

首次实机测试时应携带遥控器并保持物理急停能力。导航前复测机器人外形；当前 Nav2 footprint 为 `0.80 × 0.52 m`，障碍额外膨胀 `0.15 m`。若实测尺寸更大，应修改 `config/nav2.yaml` 和任务管理器参数，只增大安全范围。

## 测试

纯算法测试不依赖正在运行的 ROS 图：

```bash
cd ~/桌面/robot/detection/ros2_ws/src/go2_scissors_search
python3 -m pytest -q
```

测试覆盖射线与墙面求交、竖直墙面 RANSAC、时序确认、目标聚类、前沿和墙面观察位姿、安全 footprint、Unitree JSON、限速和全部 watchdog。移动状态下的时间同步稳定性仍需使用实机录包回放验证。
