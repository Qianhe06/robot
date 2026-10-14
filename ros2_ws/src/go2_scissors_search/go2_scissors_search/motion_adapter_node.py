#!/usr/bin/env python3
"""Safely adapt Nav2 velocity commands to Unitree obstacle avoidance."""

import itertools

import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image, PointCloud2
from std_srvs.srv import SetBool, Trigger
from unitree_api.msg import Request, Response

from .motion_core import MotionSafetyController
from .motion_core import obstacle_enable_payload
from .motion_core import obstacle_move_payload
from .motion_core import remote_api_payload
from .motion_core import VelocityCommand


OBSTACLE_SWITCH_API = 1001
OBSTACLE_MOVE_API = 1003
OBSTACLE_REMOTE_SOURCE_API = 1004
SPORT_STOP_MOVE_API = 1003


class MotionAdapterNode(Node):
    """Publish bounded commands and latch zero motion on any safety fault."""

    def __init__(self):
        super().__init__("go2_motion_adapter")
        self.declare_parameter("max_linear", 0.25)
        self.declare_parameter("max_angular", 0.40)
        self.declare_parameter("command_timeout", 0.30)
        self.declare_parameter("odom_timeout", 0.50)
        self.declare_parameter("cloud_timeout", 0.50)
        self.declare_parameter("camera_timeout", 1.00)
        self.declare_parameter("interface_timeout", 1.00)

        parameter = self.get_parameter
        self.safety = MotionSafetyController(
            max_linear=parameter("max_linear").value,
            max_angular=parameter("max_angular").value,
            command_timeout=parameter("command_timeout").value,
            odom_timeout=parameter("odom_timeout").value,
            cloud_timeout=parameter("cloud_timeout").value,
            camera_timeout=parameter("camera_timeout").value,
            interface_timeout=parameter("interface_timeout").value,
        )
        self.request_ids = itertools.count(1)
        self.last_stop_reason = None

        self.obstacle_request_pub = self.create_publisher(
            Request,
            "/api/obstacles_avoid/request",
            10,
        )
        self.sport_request_pub = self.create_publisher(
            Request,
            "/api/sport/request",
            10,
        )
        self.create_subscription(Twist, "/cmd_vel", self.cmd_callback, 10)
        self.create_subscription(
            Odometry,
            "/sensors/odom_synced",
            lambda _: self.mark_stream("odom"),
            qos_profile_sensor_data,
        )
        self.create_subscription(
            PointCloud2,
            "/sensors/cloud_base_synced",
            lambda _: self.mark_stream("cloud"),
            qos_profile_sensor_data,
        )
        self.create_subscription(
            Image,
            "/camera/image_raw",
            lambda _: self.mark_stream("camera"),
            qos_profile_sensor_data,
        )
        self.create_subscription(
            Response,
            "/api/obstacles_avoid/response",
            self.interface_response_callback,
            10,
        )
        self.create_subscription(
            Response,
            "/api/sport/response",
            self.interface_response_callback,
            10,
        )
        self.create_service(
            SetBool,
            "/go2_motion/enable",
            self.enable_callback,
        )
        self.create_service(
            Trigger,
            "/go2_motion/emergency_stop",
            self.emergency_stop_callback,
        )
        self.create_timer(0.05, self.control_tick)
        self.create_timer(0.50, self.configure_robot_interfaces)

        self.configure_robot_interfaces()
        self.get_logger().warning(
            "Motion starts disabled; call /go2_motion/enable after all "
            "streams and Unitree responses are healthy"
        )

    def now_seconds(self):
        return self.get_clock().now().nanoseconds * 1e-9

    def make_request(self, api_id, parameter=""):
        request = Request()
        request.header.identity.id = next(self.request_ids)
        request.header.identity.api_id = int(api_id)
        request.header.policy.priority = 0
        request.header.policy.noreply = False
        request.parameter = parameter
        return request

    def configure_robot_interfaces(self):
        self.obstacle_request_pub.publish(self.make_request(
            OBSTACLE_REMOTE_SOURCE_API,
            remote_api_payload(True),
        ))
        self.obstacle_request_pub.publish(self.make_request(
            OBSTACLE_SWITCH_API,
            obstacle_enable_payload(True),
        ))

    def mark_stream(self, name):
        self.safety.update_stream(name, self.now_seconds())

    def interface_response_callback(self, message):
        if message.header.status.code != 0:
            self.safety.emergency_stop(
                f"Unitree interface error {message.header.status.code}"
            )
            return
        self.mark_stream("interface")

    def cmd_callback(self, message):
        command = VelocityCommand(
            x=message.linear.x,
            y=message.linear.y,
            yaw=message.angular.z,
        )
        try:
            self.safety.set_command(command, self.now_seconds())
        except ValueError as error:
            self.safety.emergency_stop(f"invalid velocity command: {error}")

    def enable_callback(self, request, response):
        if request.data:
            self.configure_robot_interfaces()
            response.success, response.message = self.safety.enable(
                self.now_seconds()
            )
            if response.success:
                self.last_stop_reason = None
        else:
            self.safety.disable("disabled by operator")
            self.publish_stop("disabled by operator")
            response.success = True
            response.message = "motion disabled"
        return response

    def emergency_stop_callback(self, request, response):
        del request
        self.safety.emergency_stop("operator emergency stop")
        self.publish_stop("operator emergency stop")
        response.success = True
        response.message = "motion locked; explicit re-enable required"
        return response

    def publish_stop(self, reason):
        zero = VelocityCommand()
        self.obstacle_request_pub.publish(self.make_request(
            OBSTACLE_MOVE_API,
            obstacle_move_payload(zero),
        ))
        self.sport_request_pub.publish(self.make_request(SPORT_STOP_MOVE_API))
        if reason != self.last_stop_reason:
            self.get_logger().error(f"Motion stopped: {reason}")
            self.last_stop_reason = reason

    def control_tick(self):
        decision = self.safety.evaluate(self.now_seconds())
        self.obstacle_request_pub.publish(self.make_request(
            OBSTACLE_MOVE_API,
            obstacle_move_payload(decision.command),
        ))
        if not decision.enabled:
            self.publish_stop(decision.fault_reason or "motion disabled")


def main(args=None):
    rclpy.init(args=args)
    node = MotionAdapterNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        # The ROS signal handler may already have invalidated the context.
        # Publishing after that point raises RCLError and cannot deliver a
        # stop command. Normal KeyboardInterrupt shutdown still publishes it.
        if rclpy.ok():
            node.publish_stop("motion adapter shutdown")
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
