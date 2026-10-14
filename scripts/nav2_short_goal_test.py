#!/usr/bin/env python3
"""Send one short, guarded Nav2 goal in the robot's forward direction."""

import argparse
import math
import sys
import time

from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import ComputePathToPose, NavigateToPose
import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.time import Time
from std_srvs.srv import SetBool, Trigger
from tf2_ros import Buffer, TransformException, TransformListener


class ShortGoalTest(Node):
    """Plan first, then drive a short distance with bounded run time."""

    def __init__(self, distance, timeout):
        super().__init__("go2_nav2_short_goal_test")
        self.distance = float(distance)
        self.timeout = float(timeout)
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.path_client = ActionClient(
            self,
            ComputePathToPose,
            "/compute_path_to_pose",
        )
        self.navigation_client = ActionClient(
            self,
            NavigateToPose,
            "/navigate_to_pose",
        )
        self.motion_client = self.create_client(
            SetBool,
            "/go2_motion/enable",
        )
        self.emergency_client = self.create_client(
            Trigger,
            "/go2_motion/emergency_stop",
        )
        self.navigation_handle = None
        self.navigation_finished = False
        self.motion_enabled = False
        self.last_feedback_time = 0.0

    def wait_future(self, future, timeout, description):
        """Spin until a future completes or raise a descriptive timeout."""
        deadline = time.monotonic() + float(timeout)
        while rclpy.ok() and not future.done():
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise TimeoutError(f"{description} timeout")
            rclpy.spin_once(self, timeout_sec=min(0.10, remaining))
        if not future.done():
            raise RuntimeError(f"{description} interrupted")
        return future.result()

    def wait_for_interfaces(self):
        """Wait for every action and service used by the test."""
        if not self.path_client.wait_for_server(timeout_sec=8.0):
            raise RuntimeError("/compute_path_to_pose unavailable")
        if not self.navigation_client.wait_for_server(timeout_sec=8.0):
            raise RuntimeError("/navigate_to_pose unavailable")
        if not self.motion_client.wait_for_service(timeout_sec=8.0):
            raise RuntimeError("/go2_motion/enable unavailable")
        if not self.emergency_client.wait_for_service(timeout_sec=8.0):
            raise RuntimeError("/go2_motion/emergency_stop unavailable")

    def current_transform(self):
        """Return the latest map-to-base transform after a bounded wait."""
        deadline = time.monotonic() + 8.0
        last_error = None
        while rclpy.ok() and time.monotonic() < deadline:
            try:
                return self.tf_buffer.lookup_transform(
                    "map",
                    "base_footprint",
                    Time(),
                )
            except TransformException as error:
                last_error = error
                rclpy.spin_once(self, timeout_sec=0.10)
        raise RuntimeError(f"map -> base_footprint unavailable: {last_error}")

    def make_goal(self):
        """Build a map-frame goal directly ahead of the current robot pose."""
        transform = self.current_transform().transform
        rotation = transform.rotation
        yaw = math.atan2(
            2.0 * (rotation.w * rotation.z + rotation.x * rotation.y),
            1.0 - 2.0 * (rotation.y * rotation.y + rotation.z * rotation.z),
        )
        goal = PoseStamped()
        goal.header.frame_id = "map"
        goal.header.stamp = self.get_clock().now().to_msg()
        goal.pose.position.x = (
            transform.translation.x + self.distance * math.cos(yaw)
        )
        goal.pose.position.y = (
            transform.translation.y + self.distance * math.sin(yaw)
        )
        goal.pose.orientation.z = math.sin(0.5 * yaw)
        goal.pose.orientation.w = math.cos(0.5 * yaw)
        print(
            "Nav2 target: "
            f"x={goal.pose.position.x:.3f}, "
            f"y={goal.pose.position.y:.3f}, "
            f"distance={self.distance:.2f} m"
        )
        return goal

    def plan(self, target):
        """Require a non-empty global plan before motion can be enabled."""
        goal = ComputePathToPose.Goal()
        goal.goal = target
        goal.use_start = False
        handle = self.wait_future(
            self.path_client.send_goal_async(goal),
            8.0,
            "path goal response",
        )
        if handle is None or not handle.accepted:
            raise RuntimeError("Nav2 rejected the path-planning request")
        wrapped = self.wait_future(
            handle.get_result_async(),
            12.0,
            "path planning",
        )
        if wrapped.status != GoalStatus.STATUS_SUCCEEDED:
            raise RuntimeError(
                f"path planning failed with status {wrapped.status}"
            )
        poses = wrapped.result.path.poses
        if len(poses) < 2:
            raise RuntimeError("Nav2 returned an empty path")
        path_length = 0.0
        for first, second in zip(poses, poses[1:]):
            dx = second.pose.position.x - first.pose.position.x
            dy = second.pose.position.y - first.pose.position.y
            path_length += math.hypot(dx, dy)
        print(f"Path accepted: {len(poses)} poses, {path_length:.3f} m")

    def set_motion(self, enabled):
        """Enable or disable the guarded Unitree motion adapter."""
        request = SetBool.Request()
        request.data = bool(enabled)
        response = self.wait_future(
            self.motion_client.call_async(request),
            8.0,
            "motion service",
        )
        if response is None or not response.success:
            message = "no response" if response is None else response.message
            raise RuntimeError(f"motion service rejected request: {message}")
        self.motion_enabled = bool(enabled)
        print(response.message)

    def feedback(self, message):
        """Print navigation distance at most once per second."""
        now = time.monotonic()
        if now - self.last_feedback_time < 1.0:
            return
        self.last_feedback_time = now
        remaining = message.feedback.distance_remaining
        print(f"Distance remaining: {remaining:.3f} m")

    def navigate(self, target):
        """Run the short navigation goal with a hard timeout."""
        goal = NavigateToPose.Goal()
        goal.pose = target
        self.navigation_handle = self.wait_future(
            self.navigation_client.send_goal_async(
                goal,
                feedback_callback=self.feedback,
            ),
            8.0,
            "navigation goal response",
        )
        if (
            self.navigation_handle is None
            or not self.navigation_handle.accepted
        ):
            raise RuntimeError("Nav2 rejected the navigation goal")
        wrapped = self.wait_future(
            self.navigation_handle.get_result_async(),
            self.timeout,
            "navigation",
        )
        self.navigation_finished = True
        if wrapped.status != GoalStatus.STATUS_SUCCEEDED:
            raise RuntimeError(
                f"navigation failed with status {wrapped.status}"
            )
        print("Nav2 short goal succeeded")

    def emergency_stop(self):
        """Cancel an active goal and request the latched motion stop."""
        if self.navigation_handle is not None and not self.navigation_finished:
            try:
                self.wait_future(
                    self.navigation_handle.cancel_goal_async(),
                    3.0,
                    "goal cancellation",
                )
            except Exception as error:
                print(f"Goal cancellation warning: {error}", file=sys.stderr)
        if self.emergency_client.service_is_ready():
            try:
                response = self.wait_future(
                    self.emergency_client.call_async(Trigger.Request()),
                    5.0,
                    "emergency stop",
                )
                if response is not None:
                    print(response.message)
            except Exception as error:
                print(f"Emergency stop warning: {error}", file=sys.stderr)
        if self.motion_client.service_is_ready():
            try:
                request = SetBool.Request()
                request.data = False
                self.wait_future(
                    self.motion_client.call_async(request),
                    5.0,
                    "motion disable",
                )
            except Exception as error:
                print(f"Motion disable warning: {error}", file=sys.stderr)
        self.motion_enabled = False

    def run(self):
        """Execute the complete plan-before-motion test sequence."""
        self.wait_for_interfaces()
        target = self.make_goal()
        self.plan(target)
        self.set_motion(True)
        self.navigate(target)
        self.set_motion(False)


def parse_args(argv):
    """Parse bounded test parameters."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--distance", type=float, default=0.25)
    parser.add_argument("--timeout", type=float, default=25.0)
    arguments = parser.parse_args(argv)
    if not 0.10 <= arguments.distance <= 0.30:
        parser.error("distance must be between 0.10 and 0.30 metres")
    if not 5.0 <= arguments.timeout <= 30.0:
        parser.error("timeout must be between 5 and 30 seconds")
    return arguments


def main(argv=None):
    """Run the short-goal test and always leave motion disabled."""
    arguments = parse_args(argv)
    rclpy.init()
    node = ShortGoalTest(arguments.distance, arguments.timeout)
    exit_code = 0
    try:
        node.run()
    except KeyboardInterrupt:
        print("Test interrupted by operator", file=sys.stderr)
        exit_code = 130
    except Exception as error:
        print(f"Test failed: {error}", file=sys.stderr)
        exit_code = 1
    finally:
        if rclpy.ok():
            node.emergency_stop()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
