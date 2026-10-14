from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import ExecuteProcess, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node


def generate_launch_description():
    package_share = Path(
        get_package_share_directory("go2_scissors_search")
    )
    nav2_share = Path(get_package_share_directory("nav2_bringup"))
    detector_script = next(
        (
            parent / "go2_scissors_rgb_lidar.py"
            for parent in package_share.parents
            if (parent / "go2_scissors_rgb_lidar.py").is_file()
        ),
        Path.home()
        / "桌面"
        / "robot"
        / "detection"
        / "go2_scissors_rgb_lidar.py",
    )

    return LaunchDescription([
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                str(package_share / "launch" / "sensor_slam.launch.py")
            ),
        ),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                str(nav2_share / "launch" / "navigation_launch.py")
            ),
            launch_arguments={
                "use_sim_time": "false",
                "autostart": "true",
                "params_file": str(package_share / "config" / "nav2.yaml"),
            }.items(),
        ),
        Node(
            package="go2_scissors_search",
            executable="motion_adapter_node",
            name="go2_motion_adapter",
            output="screen",
        ),
        Node(
            package="go2_scissors_search",
            executable="mission_manager_node",
            name="scissors_search_manager",
            output="screen",
        ),
        ExecuteProcess(
            cmd=["python3", str(detector_script)],
            output="screen",
        ),
    ])
