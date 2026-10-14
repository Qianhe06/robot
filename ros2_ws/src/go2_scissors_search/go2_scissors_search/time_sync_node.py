#!/usr/bin/env python3

from collections import deque
import math

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data

from nav_msgs.msg import Odometry
from sensor_msgs.msg import Image, PointCloud2
from std_msgs.msg import Float64


def stamp_to_seconds(stamp):
    return stamp.sec + stamp.nanosec * 1e-9


def set_stamp(stamp, value):
    sec = math.floor(value)
    nanosec = round((value - sec) * 1e9)

    if nanosec >= 1_000_000_000:
        sec += 1
        nanosec -= 1_000_000_000

    stamp.sec = int(sec)
    stamp.nanosec = int(nanosec)


class TimeSyncNode(Node):
    def __init__(self):
        super().__init__("sensor_time_sync")

        self.declare_parameter("window_size", 200)
        window_size = self.get_parameter("window_size").value

        self.offset_samples = deque(maxlen=window_size)
        self.robot_offset = None
        self.camera_offset = None
        self.last_stamp = {
            "cloud": None,
            "odom": None,
        }

        self.cloud_pub = self.create_publisher(
            PointCloud2,
            "/sensors/cloud_base_synced",
            qos_profile_sensor_data,
        )

        self.odom_pub = self.create_publisher(
            Odometry,
            "/sensors/odom_synced",
            qos_profile_sensor_data,
        )

        self.offset_pub = self.create_publisher(
            Float64,
            "/sensors/robot_clock_offset",
            10,
        )

        self.create_subscription(
            PointCloud2,
            "/utlidar/cloud_base",
            self.cloud_callback,
            qos_profile_sensor_data,
        )

        self.create_subscription(
            Odometry,
            "/utlidar/robot_odom",
            self.odom_callback,
            qos_profile_sensor_data,
        )

        self.create_subscription(
            Image,
            "/camera/image_raw",
            self.camera_callback,
            qos_profile_sensor_data,
        )

        self.create_timer(2.0, self.report_status)

        self.get_logger().info("传感器时间校正节点已启动")

    def now_seconds(self):
        return self.get_clock().now().nanoseconds * 1e-9

    def update_robot_offset(self, stamp):
        observed_offset = self.now_seconds() - stamp_to_seconds(stamp)
        self.offset_samples.append(observed_offset)

        # 取窗口内最小值，减少网络传输延迟对时钟差估计的影响。
        self.robot_offset = min(self.offset_samples)

        message = Float64()
        message.data = self.robot_offset
        self.offset_pub.publish(message)

    def correct_stamp(self, stamp, stream_name):
        self.update_robot_offset(stamp)

        corrected = stamp_to_seconds(stamp) + self.robot_offset
        previous = self.last_stamp[stream_name]

        # 保证同一话题时间戳单调递增。
        if previous is not None and corrected <= previous:
            corrected = previous + 1e-9

        self.last_stamp[stream_name] = corrected
        set_stamp(stamp, corrected)

    def cloud_callback(self, message):
        self.correct_stamp(message.header.stamp, "cloud")
        self.cloud_pub.publish(message)

    def odom_callback(self, message):
        self.correct_stamp(message.header.stamp, "odom")
        self.odom_pub.publish(message)

    def camera_callback(self, message):
        self.camera_offset = (
            self.now_seconds() - stamp_to_seconds(message.header.stamp)
        )

    def report_status(self):
        if self.robot_offset is None:
            self.get_logger().warning("尚未收到机器人点云或里程计")
            return

        camera_text = "尚未收到相机"
        if self.camera_offset is not None:
            camera_text = f"{self.camera_offset:.3f}s"

        self.get_logger().info(
            f"机器人时钟校正量={self.robot_offset:.3f}s，"
            f"相机接收偏差={camera_text}"
        )


def main(args=None):
    rclpy.init(args=args)
    node = TimeSyncNode()

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
