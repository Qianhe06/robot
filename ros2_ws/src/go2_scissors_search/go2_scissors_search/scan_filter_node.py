#!/usr/bin/env python3
"""Remove LiDAR reflections from the robot body before SLAM and Nav2."""

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan

from .navigation_core import remove_robot_returns


class ScanFilterNode(Node):
    def __init__(self):
        super().__init__("go2_scan_filter")
        self.publisher = self.create_publisher(
            LaserScan, "/scan", 10
        )
        self.create_subscription(
            LaserScan, "/scan_raw", self.scan_callback,
            qos_profile_sensor_data,
        )

    def scan_callback(self, message):
        if message.header.frame_id != "base_footprint":
            self.get_logger().error(
                "Expected /scan_raw in base_footprint frame"
            )
            return
        message.ranges = remove_robot_returns(
            message.ranges,
            message.angle_min,
            message.angle_increment,
        )
        self.publisher.publish(message)


def main(args=None):
    rclpy.init(args=args)
    node = ScanFilterNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
