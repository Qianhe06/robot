from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    package_share = Path(
        get_package_share_directory("go2_scissors_search")
    )

    slam_config = str(
        package_share / "config" / "slam_toolbox.yaml"
    )

    return LaunchDescription([
        Node(
            package="go2_scissors_search",
            executable="time_sync_node",
            name="sensor_time_sync",
            output="screen",
            parameters=[{
                "window_size": 200,
            }],
        ),

        Node(
            package="go2_scissors_search",
            executable="odom_tf_node",
            name="odom_tf_broadcaster",
            output="screen",
        ),

        # 标定结果：camera_optical_frame 在 base_link 中的位姿。
        Node(
            package="tf2_ros",
            executable="static_transform_publisher",
            name="camera_static_tf",
            output="screen",
            arguments=[
                "--x", "0.357167275",
                "--y", "0.013332368",
                "--z", "-0.012932155",
                "--qx", "-0.50613947",
                "--qy", "0.46129457",
                "--qz", "-0.51088535",
                "--qw", "0.51964057",
                "--frame-id", "base_link",
                "--child-frame-id", "camera_optical_frame",
            ],
        ),

        Node(
            package="pointcloud_to_laserscan",
            executable="pointcloud_to_laserscan_node",
            name="pointcloud_to_laserscan",
            output="screen",
            remappings=[
                ("cloud_in", "/sensors/cloud_base_synced"),
                ("scan", "/scan_raw"),
            ],
            parameters=[{
                "target_frame": "base_footprint",
                "transform_tolerance": 0.05,
                # Exclude floor returns in the ground-level base_footprint frame.
                "min_height": 0.08,
                "max_height": 0.50,
                "angle_min": -3.14159265,
                "angle_max": 3.14159265,
                "angle_increment": 0.00872665,
                "scan_time": 0.065,
                "range_min": 0.20,
                "range_max": 8.00,
                "use_inf": True,
                "inf_epsilon": 1.0,
            }],
        ),

        Node(
            package="go2_scissors_search",
            executable="scan_filter_node",
            name="go2_scan_filter",
            output="screen",
        ),

        Node(
            package="slam_toolbox",
            executable="async_slam_toolbox_node",
            name="slam_toolbox",
            output="screen",
            parameters=[slam_config],
        ),
    ])
