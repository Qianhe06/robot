from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
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
    ])
