#!/usr/bin/env bash

source /home/user/miniconda3/etc/profile.d/conda.sh
conda activate robot

# 禁止误用 ~/.local 中的 Python 包
export PYTHONNOUSERSITE=1

source /opt/ros/humble/setup.bash
source /home/user/桌面/robot/detection/unitree_ros2/cyclonedds_ws/install/setup.bash

# 项目 ROS2 工作空间构建后自动加载
PROJECT_SETUP=/home/user/桌面/robot/detection/ros2_ws/install/setup.bash
if [ -f "$PROJECT_SETUP" ]; then
    source "$PROJECT_SETUP"
fi

export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI='<CycloneDDS><Domain><General><Interfaces><NetworkInterface name="eno1" priority="default" multicast="default" /></Interfaces></General></Domain></CycloneDDS>'

echo "环境已加载"
echo "Conda：$CONDA_DEFAULT_ENV"
echo "Python：$(which python)"
echo "ROS2：$ROS_DISTRO"
