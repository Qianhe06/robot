#!/usr/bin/env python3
"""Publish the JPEG stream written by the native Go2 camera client."""

import os
from pathlib import Path

import cv2
import rclpy
from cv_bridge import CvBridge
from rclpy.node import Node
from sensor_msgs.msg import Image


class Go2CameraBridge(Node):
    """Convert the latest camera JPEG into a ROS Image message."""

    def __init__(self):
        super().__init__("go2_camera_bridge")
        self.image_path = Path("/tmp/go2_camera/latest.jpg")
        self.publisher = self.create_publisher(
            Image, "/camera/image_raw", 10
        )
        self.bridge = CvBridge()
        self.last_mtime_ns = 0
        self.create_timer(1.0 / 30.0, self.publish_latest_image)
        self.get_logger().info(
            f"Watching {self.image_path} and publishing /camera/image_raw"
        )

    def publish_latest_image(self):
        try:
            mtime_ns = os.stat(self.image_path).st_mtime_ns
        except FileNotFoundError:
            return

        if mtime_ns == self.last_mtime_ns:
            return

        image = cv2.imread(str(self.image_path), cv2.IMREAD_COLOR)
        if image is None:
            self.get_logger().warning("Could not decode the latest JPEG")
            return

        self.last_mtime_ns = mtime_ns
        message = self.bridge.cv2_to_imgmsg(image, encoding="bgr8")
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = "camera_optical_frame"
        self.publisher.publish(message)


def main(args=None):
    rclpy.init(args=args)
    node = Go2CameraBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
