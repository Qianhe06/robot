#!/usr/bin/env python3
"""Publish the calibrated Go2 front-camera model as CameraInfo."""

from pathlib import Path

import rclpy
import yaml
from rclpy.node import Node
from sensor_msgs.msg import CameraInfo


class Go2CameraInfoPublisher(Node):
    """Publish camera calibration from go2_camera_parameters.yaml."""

    def __init__(self):
        super().__init__("go2_camera_info_publisher")
        config_path = Path(__file__).with_name("go2_camera_parameters.yaml")
        with config_path.open("r", encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file)

        self.frame_id = config["camera_frame"]
        self.message = CameraInfo()
        self.message.width = int(config["image_width"])
        self.message.height = int(config["image_height"])
        self.message.distortion_model = config["distortion_model"]
        self.message.d = [
            float(value)
            for value in config["distortion_coefficients"]["data"]
        ]
        self.message.k = [
            float(value) for value in config["camera_matrix"]["data"]
        ]
        self.message.r = [
            float(value) for value in config["rectification_matrix"]["data"]
        ]
        self.message.p = [
            float(value) for value in config["projection_matrix"]["data"]
        ]

        self.publisher = self.create_publisher(
            CameraInfo, "/camera/camera_info", 10
        )
        self.create_timer(1.0 / 15.0, self.publish_camera_info)
        self.get_logger().info(
            f"Loaded {config_path} and publishing /camera/camera_info"
        )

    def publish_camera_info(self):
        self.message.header.stamp = self.get_clock().now().to_msg()
        self.message.header.frame_id = self.frame_id
        self.publisher.publish(self.message)


def main(args=None):
    rclpy.init(args=args)
    node = Go2CameraInfoPublisher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
