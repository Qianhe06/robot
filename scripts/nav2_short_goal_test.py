#!/usr/bin/env python3
"""Send one short, guarded Nav2 goal in the robot's forward direction."""

import argparse
import math
import sys
import time

from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped, Twist
from nav2_msgs.action import ComputePathToPose, NavigateToPose
from nav2_msgs.msg import Costmap
from nav_msgs.msg import OccupancyGrid, Odometry
import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.time import Time
from std_srvs.srv import SetBool, Trigger
from tf2_ros import Buffer, TransformException, TransformListener


class ShortGoalTest(Node):
    """Plan first, then drive a short distance with bounded run time."""

    def __init__(self, distance, timeout, dry_run_seconds=0.0):
        super().__init__("go2_nav2_short_goal_test")
        self.distance = float(distance)
        self.timeout = float(timeout)
        self.dry_run_seconds = float(dry_run_seconds)
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
        self.command_samples = []
        self.start_map_pose = None
        self.start_odom_pose = None
        self.latest_odom_pose = None
        self.map_message = None
        self.costmap_message = None
        self.create_subscription(Twist, "/cmd_vel", self.command_callback, 50)
        self.create_subscription(
            Odometry,
            "/sensors/odom_synced",
            self.odom_callback,
            50,
        )
        self.create_subscription(
            OccupancyGrid,
            "/map",
            self.map_callback,
            10,
        )
        self.create_subscription(
            Costmap,
            "/global_costmap/costmap_raw",
            self.costmap_callback,
            10,
        )

    def map_callback(self, message):
        """Retain the latest SLAM occupancy grid for diagnostics."""
        self.map_message = message

    def costmap_callback(self, message):
        """Retain the latest global costmap for diagnostics."""
        self.costmap_message = message

    def command_callback(self, message):
        """Record the final velocity stream received by the motion adapter."""
        self.command_samples.append((
            float(message.linear.x),
            float(message.linear.y),
            float(message.angular.z),
        ))
        if len(self.command_samples) > 2000:
            del self.command_samples[:1000]

    @staticmethod
    def pose_values(pose):
        """Return planar position and yaw from a ROS pose."""
        if hasattr(pose, "position"):
            position = pose.position
            orientation = pose.orientation
        else:
            position = pose.translation
            orientation = pose.rotation
        yaw = math.atan2(
            2.0 * (
                orientation.w * orientation.z
                + orientation.x * orientation.y
            ),
            1.0 - 2.0 * (
                orientation.y * orientation.y
                + orientation.z * orientation.z
            ),
        )
        return float(position.x), float(position.y), float(yaw)

    def odom_callback(self, message):
        """Retain Unitree odometry independently of SLAM corrections."""
        self.latest_odom_pose = self.pose_values(message.pose.pose)

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
        deadline = time.monotonic() + 6.0
        while (
            rclpy.ok()
            and time.monotonic() < deadline
            and (
                self.map_message is None
                or self.costmap_message is None
                or self.latest_odom_pose is None
            )
        ):
            rclpy.spin_once(self, timeout_sec=0.10)
        if self.map_message is None:
            raise RuntimeError("/map data unavailable")
        if self.costmap_message is None:
            raise RuntimeError("/global_costmap/costmap_raw data unavailable")
        if self.latest_odom_pose is None:
            raise RuntimeError("/sensors/odom_synced data unavailable")

    @staticmethod
    def grid_value(message, x, y):
        """Return one grid value at a world coordinate or outside."""
        if isinstance(message, OccupancyGrid):
            metadata = message.info
            width = int(metadata.width)
            height = int(metadata.height)
        else:
            metadata = message.metadata
            width = int(metadata.size_x)
            height = int(metadata.size_y)
        resolution = float(metadata.resolution)
        origin = metadata.origin.position
        column = math.floor((float(x) - origin.x) / resolution)
        row = math.floor((float(y) - origin.y) / resolution)
        if column < 0 or row < 0 or column >= width or row >= height:
            return "outside"
        return int(message.data[row * width + column])

    def print_grid_values(self, label, x, y):
        """Print SLAM occupancy and global cost for one world point."""
        occupancy = self.grid_value(self.map_message, x, y)
        cost = self.grid_value(self.costmap_message, x, y)
        print(
            f"Grid {label}: x={x:.3f}, y={y:.3f}, "
            f"map={occupancy}, global_cost={cost}"
        )

    def require_safe_target(self, target):
        """Reject a target inside occupied or non-traversable map cells."""
        x = target.pose.position.x
        y = target.pose.position.y
        occupancy = self.grid_value(self.map_message, x, y)
        cost = self.grid_value(self.costmap_message, x, y)
        if not isinstance(occupancy, int) or not 0 <= occupancy < 25:
            raise RuntimeError(
                f"target is not known free space (map={occupancy})"
            )
        if not isinstance(cost, int) or cost >= 253:
            raise RuntimeError(
                "target is not traversable in the global costmap "
                f"(cost={cost}; 253-255 are forbidden)"
            )

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

    def make_goal(self, yaw_offset=0.0):
        """Build a nearby map-frame goal at a bounded heading offset."""
        transform = self.current_transform().transform
        rotation = transform.rotation
        yaw = math.atan2(
            2.0 * (rotation.w * rotation.z + rotation.x * rotation.y),
            1.0 - 2.0 * (rotation.y * rotation.y + rotation.z * rotation.z),
        )
        goal_yaw = yaw + float(yaw_offset)
        self.start_map_pose = (
            float(transform.translation.x),
            float(transform.translation.y),
            float(yaw),
        )
        self.start_odom_pose = self.latest_odom_pose
        self.print_grid_values(
            "start",
            transform.translation.x,
            transform.translation.y,
        )
        goal = PoseStamped()
        goal.header.frame_id = "map"
        goal.header.stamp = self.get_clock().now().to_msg()
        goal.pose.position.x = (
            transform.translation.x + self.distance * math.cos(goal_yaw)
        )
        goal.pose.position.y = (
            transform.translation.y + self.distance * math.sin(goal_yaw)
        )
        goal.pose.orientation.z = math.sin(0.5 * goal_yaw)
        goal.pose.orientation.w = math.cos(0.5 * goal_yaw)
        print(
            "Nav2 target: "
            f"x={goal.pose.position.x:.3f}, "
            f"y={goal.pose.position.y:.3f}, "
            f"distance={self.distance:.2f} m, "
            f"heading_offset={math.degrees(yaw_offset):.0f} deg"
        )
        self.print_grid_values(
            f"target {math.degrees(yaw_offset):+.0f} deg",
            goal.pose.position.x,
            goal.pose.position.y,
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
            error_code = getattr(wrapped.result, "error_code", "unknown")
            raise RuntimeError(
                f"path planning failed with status {wrapped.status}, "
                f"error_code {error_code}"
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

    def select_plannable_goal(self):
        """Require a reachable target directly ahead of the robot."""
        offsets = (0.0,)
        failures = []
        for offset_degrees in offsets:
            offset = math.radians(offset_degrees)
            target = self.make_goal(offset)
            try:
                self.require_safe_target(target)
                self.plan(target)
                return target
            except Exception as error:
                failures.append(f"{offset_degrees:+.0f} deg: {error}")
                print(f"Candidate rejected: {failures[-1]}")
        details = "; ".join(failures)
        raise RuntimeError(f"no reachable straight test target: {details}")

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

    def send_navigation_goal(self, target):
        """Send the short navigation goal and retain its handle."""
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
        return self.navigation_handle.get_result_async()

    def navigate(self, target):
        """Run the short navigation goal with a hard timeout."""
        result_future = self.send_navigation_goal(target)
        wrapped = self.wait_future(
            result_future,
            self.timeout,
            "navigation",
        )
        self.navigation_finished = True
        if wrapped.status != GoalStatus.STATUS_SUCCEEDED:
            raise RuntimeError(
                f"navigation failed with status {wrapped.status}"
            )
        print("Nav2 short goal succeeded")

    def verify_map_displacement(self):
        """Let SLAM settle, then require meaningful physical displacement."""
        print("Waiting 3 seconds for SLAM correction...")
        deadline = time.monotonic() + 3.0
        while rclpy.ok() and time.monotonic() < deadline:
            rclpy.spin_once(self, timeout_sec=0.10)
        transform = self.current_transform().transform
        final_x = float(transform.translation.x)
        final_y = float(transform.translation.y)
        start_x, start_y, _ = self.start_map_pose
        displacement = math.hypot(final_x - start_x, final_y - start_y)
        print(
            f"SLAM displacement: {displacement:.3f} m "
            f"(start=({start_x:.3f}, {start_y:.3f}), "
            f"end=({final_x:.3f}, {final_y:.3f}))"
        )
        minimum = max(0.10, self.distance * 0.60)
        if displacement < minimum:
            raise RuntimeError(
                f"SLAM displacement {displacement:.3f} m is below "
                f"the required {minimum:.3f} m"
            )

    def print_command_summary(self):
        """Print velocity ranges observed during navigation."""
        nonzero = [
            sample for sample in self.command_samples
            if any(abs(value) > 1e-4 for value in sample)
        ]
        print(
            f"Command samples: total={len(self.command_samples)}, "
            f"nonzero={len(nonzero)}"
        )
        if not nonzero:
            raise RuntimeError("Nav2 produced no nonzero /cmd_vel samples")
        labels = ("linear.x", "linear.y", "angular.z")
        for index, label in enumerate(labels):
            values = [sample[index] for sample in nonzero]
            print(
                f"{label}: min={min(values):.4f}, "
                f"max={max(values):.4f}, "
                f"mean={sum(values) / len(values):.4f}"
            )
        unique_samples = []
        for sample in nonzero:
            rounded = tuple(round(value, 3) for value in sample)
            if rounded not in unique_samples:
                unique_samples.append(rounded)
            if len(unique_samples) >= 12:
                break
        print(f"First distinct commands: {unique_samples}")

    @staticmethod
    def angle_difference(final, initial):
        """Return the signed shortest angular displacement."""
        return math.atan2(
            math.sin(float(final) - float(initial)),
            math.cos(float(final) - float(initial)),
        )

    def print_pose_diagnostics(self):
        """Compare SLAM and Unitree odometry after the robot is stopped."""
        if self.start_map_pose is None or self.start_odom_pose is None:
            return
        deadline = time.monotonic() + 0.5
        while rclpy.ok() and time.monotonic() < deadline:
            rclpy.spin_once(self, timeout_sec=0.05)
        transform = self.current_transform().transform
        map_final = self.pose_values(transform)
        odom_final = self.latest_odom_pose

        def print_delta(label, start, final):
            displacement = math.hypot(final[0] - start[0], final[1] - start[1])
            yaw_change = math.degrees(
                self.angle_difference(final[2], start[2])
            )
            print(
                f"{label}: displacement={displacement:.3f} m, "
                f"yaw_change={yaw_change:+.1f} deg, "
                f"start=({start[0]:.3f}, {start[1]:.3f}), "
                f"end=({final[0]:.3f}, {final[1]:.3f})"
            )

        print("===== Navigation diagnostics =====")
        print_delta("SLAM map pose", self.start_map_pose, map_final)
        print_delta("Unitree odom", self.start_odom_pose, odom_final)
        try:
            self.print_command_summary()
        except RuntimeError as error:
            print(f"Command diagnostics: {error}")

    def dry_run(self, target):
        """Collect Nav2 output while the motion adapter remains disabled."""
        self.set_motion(False)
        self.command_samples.clear()
        result_future = self.send_navigation_goal(target)
        deadline = time.monotonic() + self.dry_run_seconds
        while (
            rclpy.ok()
            and time.monotonic() < deadline
            and not result_future.done()
        ):
            rclpy.spin_once(self, timeout_sec=0.10)
        if result_future.done():
            wrapped = result_future.result()
            print(f"Dry-run navigation ended with status {wrapped.status}")
        else:
            self.wait_future(
                self.navigation_handle.cancel_goal_async(),
                3.0,
                "dry-run goal cancellation",
            )
            print("Dry-run goal cancelled as planned")
        self.navigation_finished = True

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
        target = self.select_plannable_goal()
        if self.dry_run_seconds > 0.0:
            self.dry_run(target)
            return
        self.set_motion(True)
        self.navigate(target)
        self.set_motion(False)
        self.verify_map_displacement()


def parse_args(argv):
    """Parse bounded test parameters."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--distance", type=float, default=0.25)
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--dry-run-seconds", type=float, default=0.0)
    arguments = parser.parse_args(argv)
    if not 0.10 <= arguments.distance <= 0.60:
        parser.error("distance must be between 0.10 and 0.60 metres")
    if not 5.0 <= arguments.timeout <= 30.0:
        parser.error("timeout must be between 5 and 30 seconds")
    if not 0.0 <= arguments.dry_run_seconds <= 10.0:
        parser.error("dry-run-seconds must be between 0 and 10 seconds")
    return arguments


def main(argv=None):
    """Run the short-goal test and always leave motion disabled."""
    arguments = parse_args(argv)
    rclpy.init()
    node = ShortGoalTest(
        arguments.distance,
        arguments.timeout,
        arguments.dry_run_seconds,
    )
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
            node.print_pose_diagnostics()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
