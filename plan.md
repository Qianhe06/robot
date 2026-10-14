# Go2 自主寻找并接近墙上剪刀

## Summary

构建完整 ROS2 自主闭环：Go2 在线建图并探索全部可达区域，持续检查墙面；检测到墙上用透明胶带固定的剪刀后，拟合周边墙面确定其三维位置，选择路径最近的目标，导航到代价地图允许的最近安全位置，面向剪刀停车。未找到时返回起点。

## Implementation Changes

- 建立 `go2_scissors_search` ROS2 包，统一提供 SLAM、Nav2、目标定位、区域探索、任务管理和 Unitree 运动适配的 launch/config。
- 使用 `/utlidar/cloud_base` 经高度过滤和 `pointcloud_to_laserscan` 生成 `/scan`；发布 `/utlidar/robot_odom` 对应的 `odom → base_link` TF，由 `slam_toolbox` 产生 `map → odom`。
- 使用 Nav2 的全局规划和局部代价地图，初始保守 footprint 设为约 `0.80 × 0.52 m`、额外膨胀 `0.15 m`；速度上限 `0.25 m/s`、角速度上限 `0.40 rad/s`。
- 实现 `cmd_vel` 适配节点：
  - 启动时通过 `/api/obstacles_avoid/request` 启用远程 API 和避障。
  - 将 Nav2 速度以避障接口 `Move(mode=0)`、20 Hz 发送。
  - 正常停车及故障时同时发送零速度和 `/api/sport/request` 的 `StopMove`（API 1003）。
  - 运动接口无响应、速度指令超过 0.3 秒未更新、odom/点云超过 0.5 秒未更新或相机超过 1 秒未更新时立即停车并锁定，必须重新启用后才能运动。
- 将现有剪刀检测改为可移动状态下的定位：
  - RGB、点云和 odom 按时间戳近似同步；取消未经运动补偿的 10 帧直接拼接。
  - YOLO 框只负责识别；从框周围而非透明胶带表面提取 LiDAR 点，RANSAC 拟合竖直墙面。
  - 用检测框中心相机射线与墙面求交，得到剪刀在 `map` 中的位置；透明胶带或剪刀本身无有效回波也不影响定位。
  - 静止确认阶段要求最近 5 帧中至少 3 帧检测成功、墙面支持点不少于 20、平面残差不超过 5 cm、目标位置离散不超过 25 cm；不满足则恢复搜索。
  - 保留现有 `/scissors/position_base`、`/scissors/position_odom` 和调试图输出，并新增 `/scissors/target_pose`（`PoseWithCovarianceStamped`，`map` 坐标系）。
- 搜索分两阶段：
  - 首先执行前沿探索，持续扩展在线地图；检测始终开启。
  - 连续三次没有可达前沿后，沿已建地图的墙体/障碍轮廓生成朝墙观察位姿，间隔不超过 1 m，依次巡检未覆盖墙面。
  - 到达观察点后慢速转向扫描；搜索覆盖的是地图中所有可达空间及其可见墙面，不包含门后、遮挡后或机器人无法进入的区域。
- 发现候选目标后立即取消当前导航并停车确认；多个目标按 Nav2 路径长度选择最近者。
- 从墙面法向的自由空间侧向外采样停车位，选择代价地图中离墙最近、footprint 不碰撞且存在有效路径的位置，最终朝向剪刀；不再强制固定 0.35 m。
- 若接近过程中目标位置漂移、路径失效或传感器异常，立即停车；重新确认成功后重规划，连续失败则回到搜索。
- 所有可达区域巡检完成仍未找到时，导航回任务起点并报告 `NOT_FOUND`。

## Public ROS Interfaces

- `/go2_motion/enable` — `std_srvs/SetBool`：显式允许或禁止自主运动。
- `/go2_motion/emergency_stop` — `std_srvs/Trigger`：立即停止并锁定运动。
- `/scissors_search/enable` — `std_srvs/SetBool`：开始任务或取消任务。
- `/scissors_search/status` — `diagnostic_msgs/DiagnosticArray`：发布 `IDLE`、`MAPPING`、`EXPLORING`、`INSPECTING`、`CONFIRMING`、`APPROACHING`、`FOUND`、`RETURNING`、`NOT_FOUND`、`FAULT`、`CANCELLED`。
- `/scissors_search/inspection_path` — `nav_msgs/Path`：显示待巡检观察位姿。
- `/scissors/target_pose` — `geometry_msgs/PoseWithCovarianceStamped`：确认后的墙面目标位置。
- 直接使用标准 Nav2 action；Unitree SDK 副本只作为接口参考，不修改其源码。

## Test Plan

- 单元测试：相机射线与墙面求交、墙面法向选择、目标时序确认、多目标聚类、最近可达停车位、前沿/墙面覆盖规划、Unitree JSON 请求和所有 watchdog。
- 回放测试：用录制的图像、点云和 odom 验证机器人移动时目标在 `map` 中稳定，不出现旧版 10 帧拖影。
- 分级实机验证：
  1. 架空或原地验证启用、速度限幅、零速、`StopMove` 和急停。
  2. 围栏小区域验证 SLAM、Nav2、厂商避障和返回起点。
  3. 三个不同墙面位置各测试一次剪刀定位与接近。
  4. 放置两把剪刀，验证选择路径最近者。
  5. 无剪刀、遮挡、相机断流、LiDAR 断流和目标丢失场景均应安全停车或返回。
- 验收标准：无碰墙或踩踏风险；最终姿态正对剪刀；停在代价地图允许的最近安全位姿；任一关键数据流失后 0.5 秒内停止；未找到时返回起点并发布明确结果。

## Assumptions

- 第一版用于单层室内平地，不处理楼梯、室外地形或电梯。
- 剪刀位于前置相机在巡检距离内可见的墙面高度，墙面或展示区域周边能提供足够 LiDAR 回波。
- `/camera/image_raw`、`/camera/camera_info`、`/utlidar/cloud_base`、`/utlidar/robot_odom` 和 Unitree API topics 持续可用。
- 机器人尺寸在首次实机导航前复测；若实测大于保守 footprint，则只增大 footprint，不降低安全膨胀距离。
- 操作员首次测试时携带遥控器并保持物理急停能力。
