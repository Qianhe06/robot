#!/usr/bin/env python3

import math

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data

from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from tf2_ros import TransformBroadcaster


def quaternion_multiply(first, second):
    x1, y1, z1, w1 = first
    x2, y2, z2, w2 = second

    return (
        w1*x2 + x1*w2 + y1*z2 - z1*y2,
        w1*y2 - x1*z2 + y1*w2 + z1*x2,
        w1*z2 + x1*y2 - y1*x2 + z1*w2,
        w1*w2 - x1*x2 - y1*y2 - z1*z2,
    )


def quaternion_inverse(quaternion):
    x, y, z, w = quaternion
    norm = x*x + y*y + z*z + w*w

    return (
        -x / norm,
        -y / norm,
        -z / norm,
        w / norm,
    )


def yaw_from_quaternion(x, y, z, w):
    return math.atan2(
        2.0 * (w*z + x*y),
        1.0 - 2.0 * (y*y + z*z),
    )


class OdomTfNode(Node):
    def __init__(self):
        super().__init__("odom_tf_broadcaster")

        self.broadcaster = TransformBroadcaster(self)

        self.create_subscription(
            Odometry,
            "/sensors/odom_synced",
            self.odom_callback,
            qos_profile_sensor_data,
        )

        self.get_logger().info(
            "开始发布 odom -> base_footprint -> base_link"
        )

    def odom_callback(self, message):
        position = message.pose.pose.position
        orientation = message.pose.pose.orientation

        full_quaternion = (
            orientation.x,
            orientation.y,
            orientation.z,
            orientation.w,
        )

        yaw = yaw_from_quaternion(*full_quaternion)

        yaw_quaternion = (
            0.0,
            0.0,
            math.sin(yaw / 2.0),
            math.cos(yaw / 2.0),
        )

        residual_quaternion = quaternion_multiply(
            quaternion_inverse(yaw_quaternion),
            full_quaternion,
        )

        odom_to_footprint = TransformStamped()
        odom_to_footprint.header.stamp = message.header.stamp
        odom_to_footprint.header.frame_id = "odom"
        odom_to_footprint.child_frame_id = "base_footprint"

        odom_to_footprint.transform.translation.x = position.x
        odom_to_footprint.transform.translation.y = position.y
        odom_to_footprint.transform.translation.z = 0.0

        odom_to_footprint.transform.rotation.x = 0.0
        odom_to_footprint.transform.rotation.y = 0.0
        odom_to_footprint.transform.rotation.z = yaw_quaternion[2]
        odom_to_footprint.transform.rotation.w = yaw_quaternion[3]

        footprint_to_base = TransformStamped()
        footprint_to_base.header.stamp = message.header.stamp
        footprint_to_base.header.frame_id = "base_footprint"
        footprint_to_base.child_frame_id = "base_link"

        footprint_to_base.transform.translation.x = 0.0
        footprint_to_base.transform.translation.y = 0.0
        footprint_to_base.transform.translation.z = position.z

        footprint_to_base.transform.rotation.x = residual_quaternion[0]
        footprint_to_base.transform.rotation.y = residual_quaternion[1]
        footprint_to_base.transform.rotation.z = residual_quaternion[2]
        footprint_to_base.transform.rotation.w = residual_quaternion[3]

        self.broadcaster.sendTransform([
            odom_to_footprint,
            footprint_to_base,
        ])


def main(args=None):
    rclpy.init(args=args)
    node = OdomTfNode()

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
