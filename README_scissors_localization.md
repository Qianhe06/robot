# Go2 剪刀 RGB-LiDAR 定位

`go2_scissors_rgb_lidar.py` 使用 YOLO26l 检测 RGB 图像中的剪刀，将
`/utlidar/cloud_base` 投影到图像，在检测框内选择紧凑的深度簇，并把对应
三维点标成红色。投影只用于内部的二维到三维关联；主窗口使用 Open3D 显示
三维点云，环境点为灰色、剪刀点为红色，并在剪刀点周围显示红色三维包围框。

## 完整启动流程

融合程序不会启动相机采集、相机 ROS 桥或 CameraInfo 发布器。关闭过这些上游
程序后，需要按以下顺序重新启动。

### 终端 1：Go2 原生相机采集

这个终端不要 source ROS 环境，避免 ROS 与 Unitree SDK2 使用不同版本的 DDS
动态库。

```bash
conda deactivate 2>/dev/null || true
cd ~/桌面/robot/detection/unitree_sdk2-2.0.2
export LD_LIBRARY_PATH="$PWD/thirdparty/lib/x86_64:/usr/lib/x86_64-linux-gnu"
./build/bin/go2_camera_capture eno1
```

它应持续更新 `/tmp/go2_camera/latest.jpg`。

### 终端 2：相机 ROS 桥与 CameraInfo

```bash
conda deactivate 2>/dev/null || true
source /opt/ros/humble/setup.bash
source ~/桌面/robot/detection/unitree_ros2/cyclonedds_ws/install/setup.bash

export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI='<CycloneDDS><Domain><General><Interfaces><NetworkInterface name="eno1" priority="default" multicast="default" /></Interfaces></General></Domain></CycloneDDS>'

python3 ~/桌面/robot/go2_camera_info_publisher.py &
python3 ~/桌面/robot/go2_camera_ros2_bridge.py
```

### 终端 3：检查所有输入话题

```bash
conda deactivate 2>/dev/null || true
source /opt/ros/humble/setup.bash
source ~/桌面/robot/detection/unitree_ros2/cyclonedds_ws/install/setup.bash

export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI='<CycloneDDS><Domain><General><Interfaces><NetworkInterface name="eno1" priority="default" multicast="default" /></Interfaces></General></Domain></CycloneDDS>'

timeout 5 ros2 topic hz /camera/image_raw
timeout 5 ros2 topic hz /camera/camera_info
timeout 5 ros2 topic hz /utlidar/cloud_base
timeout 5 ros2 topic hz /utlidar/robot_odom
```

前三个话题必须有数据。`/utlidar/robot_odom` 仅用于生成 odom 坐标；缺少它时，
程序仍能发布 `base_link` 下的位置。

### 终端 4：剪刀检测与三维定位

```bash
conda activate robot
source /opt/ros/humble/setup.bash
source ~/桌面/robot/detection/unitree_ros2/cyclonedds_ws/install/setup.bash

export PYTHONPATH="/opt/ros/humble/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages:$HOME/桌面/robot/detection/unitree_ros2/cyclonedds_ws/install/unitree_go/local/lib/python3.10/dist-packages:$PYTHONPATH"
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI='<CycloneDDS><Domain><General><Interfaces><NetworkInterface name="eno1" priority="default" multicast="default" /></Interfaces></General></Domain></CycloneDDS>'

cd ~/桌面/robot/detection
python go2_scissors_rgb_lidar.py
```

## 输出

- `/scissors/detection_image`：检测框、关联点和三维坐标叠加图。
- `/scissors/image_rectified`：根据 CameraInfo 内参与畸变系数校正后的纯 RGB 图像。
- `/scissors/colored_cloud`：灰色原始点云，剪刀对应点为红色。
- `/scissors/position_base`：剪刀相对机器人 `base_link` 的位置，单位为米。
- `/scissors/position_odom`：剪刀在里程计坐标系中的位置，单位为米。
- `/scissors/marker`：可直接在 RViz2 中显示的红色球形标记。

程序会同时打开 `Go2 Scissors RGB Detection` 和
`Go2 Scissors 3D Localization` 两个窗口。RGB 图中浅蓝点是全部可见 LiDAR
投影，黄点是落在剪刀检测框中的候选点，紫点是最终选中的剪刀点。

原始 `/camera/image_raw` 会先使用 `/camera/camera_info` 中的 `K` 和 `D` 参数
完成去畸变。YOLO 检测、LiDAR 投影以及最终 RGB 窗口都使用校正后的图像和新
相机矩阵，确保去畸变后检测框与投影点仍处于同一个像素坐标系。

程序始终在后台保存最新 10 个 LiDAR 消息，RGB 检测持续更新。Open3D 不会自动
更新；将焦点放在 RGB 窗口并按一次空格，才会把当时最新 10 帧合并结果以及
剪刀着色更新到 Open3D。这样三维剪刀标记不会跟随逐帧检测闪烁。检测短暂丢失
时会保留最近 0.5 秒的检测框，以减轻 RGB 框闪烁。

10 帧窗口期间如果机器人运动，未经运动补偿的直接拼接会出现重影。

RGB 窗口快捷键：空格更新 Open3D 快照，`s` 保存当前 RGB 图和点云，`q` 同时
退出两个窗口。也可直接关闭任一窗口，或在终端按 `Ctrl+C` 退出。

RViz2 的 Fixed Frame 可设为 `base_link`，显示 `/scissors/colored_cloud`；若要
观察随机器人移动仍保持相对稳定的剪刀位置，可将 Fixed Frame 设为 `odom`，
再显示 `/scissors/marker`。

## 当前参数

- 模型：`yolo26l.pt`
- 检测阈值：`0.60`
- 目标类别：`scissors`
- 外参：历史记录中的 11-pose joint 优化结果

剪刀较薄，单帧 LiDAR 可能没有直接击中剪刀。程序至少需要两个同一深度层的
点才发布位置；若窗口有检测框但没有三维坐标，应先观察紫色投影点和外参对齐，
再考虑多帧点云累积。
