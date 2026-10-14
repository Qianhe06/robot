#!/usr/bin/env python3
"""Coordinate mapping, exploration, inspection, approach, and return home."""

import math

from action_msgs.msg import GoalStatus
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
from nav2_msgs.action import ComputePathToPose, NavigateToPose, Spin
from nav_msgs.msg import OccupancyGrid, Path
import numpy as np
import rclpy
from rclpy.action import ActionClient
from rclpy.duration import Duration
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rclpy.time import Time
from std_srvs.srv import SetBool
from std_msgs.msg import Bool
from tf2_ros import Buffer, TransformException, TransformListener

from .mission_core import SearchMission, SearchState
from .navigation_core import frontier_goals, GridMap, inspection_goals
from .navigation_core import nearest_safe_standoff, Pose2D


class MissionManagerNode(Node):
    """Drive the high-level search state machine through Nav2 goals."""

    def __init__(self):
        super().__init__("scissors_search_manager")
        self.declare_parameter("frontier_min_cells", 8)
        self.declare_parameter("goal_separation", 0.50)
        self.declare_parameter("inspection_spacing", 0.80)
        self.declare_parameter("robot_length", 0.80)
        self.declare_parameter("robot_width", 0.52)
        self.declare_parameter("safety_inflation", 0.15)

        self.mission = SearchMission()
        self.grid = None
        self.start_pose = None
        self.goal_handle = None
        self.goal_pending = False
        self.goal_purpose = None
        self.goal_pose = None
        self.navigation_generation = 0
        self.scan_handle = None
        self.scan_pending = False
        self.scan_generation = 0
        self.visited_goals = []
        self.inspection_queue = []
        self.inspection_prepared = False
        self.target_candidates = []
        self.target_collection_deadline = None
        self.confirmation_deadline = None
        self.stationary_ready_time = None
        self.target_selection_started = False
        self.path_candidates = []
        self.path_results = []
        self.active_target_position = None
        self.last_candidate_time = None
        self.last_state = None

        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.navigator = ActionClient(self, NavigateToPose, "navigate_to_pose")
        self.path_planner = ActionClient(
            self,
            ComputePathToPose,
            "compute_path_to_pose",
        )
        self.spinner = ActionClient(self, Spin, "spin")
        self.motion_client = self.create_client(SetBool, "/go2_motion/enable")
        latched_qos = QoSProfile(depth=1)
        latched_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
        latched_qos.reliability = ReliabilityPolicy.RELIABLE

        self.status_pub = self.create_publisher(
            DiagnosticArray,
            "/scissors_search/status",
            latched_qos,
        )
        self.inspection_path_pub = self.create_publisher(
            Path,
            "/scissors_search/inspection_path",
            latched_qos,
        )
        self.confirmation_reset_pub = self.create_publisher(
            Bool,
            "/scissors/confirmation_reset",
            10,
        )
        self.create_subscription(OccupancyGrid, "/map", self.map_callback, 10)
        self.create_subscription(
            PoseWithCovarianceStamped,
            "/scissors/target_pose",
            self.target_callback,
            10,
        )
        self.create_subscription(
            Bool,
            "/scissors/candidate",
            self.candidate_callback,
            10,
        )
        self.create_service(
            SetBool,
            "/scissors_search/enable",
            self.enable_callback,
        )
        self.create_timer(1.0, self.tick)
        self.publish_status("ready; search disabled")

    def map_callback(self, message):
        origin = message.info.origin.position
        self.grid = GridMap(
            message.data,
            message.info.width,
            message.info.height,
            message.info.resolution,
            origin.x,
            origin.y,
        )
        if self.mission.state == SearchState.MAPPING:
            self.mission.map_ready()
            self.request_motion(True)
            self.publish_status("map available; starting frontier exploration")

    def current_pose(self):
        try:
            transform = self.tf_buffer.lookup_transform(
                "map",
                "base_footprint",
                Time(),
                timeout=Duration(seconds=0.10),
            )
        except TransformException:
            return None
        translation = transform.transform.translation
        rotation = transform.transform.rotation
        yaw = math.atan2(
            2.0 * (rotation.w * rotation.z + rotation.x * rotation.y),
            1.0 - 2.0 * (rotation.y * rotation.y + rotation.z * rotation.z),
        )
        return Pose2D(translation.x, translation.y, yaw)

    @staticmethod
    def pose_message(pose, stamp):
        message = PoseStamped()
        message.header.stamp = stamp
        message.header.frame_id = "map"
        message.pose.position.x = float(pose.x)
        message.pose.position.y = float(pose.y)
        message.pose.orientation.z = math.sin(0.5 * pose.yaw)
        message.pose.orientation.w = math.cos(0.5 * pose.yaw)
        return message

    def request_motion(self, enabled):
        if not self.motion_client.service_is_ready():
            self.get_logger().warning(
                "/go2_motion/enable service is unavailable"
            )
            return
        request = SetBool.Request()
        request.data = bool(enabled)
        future = self.motion_client.call_async(request)
        future.add_done_callback(self.motion_response)

    def motion_response(self, future):
        try:
            response = future.result()
        except Exception as error:
            self.fail(f"motion service failed: {error}")
            return
        if not response.success and self.mission.state not in (
            SearchState.IDLE,
            SearchState.CANCELLED,
            SearchState.FOUND,
            SearchState.NOT_FOUND,
            SearchState.FAULT,
        ):
            self.fail(f"motion enable rejected: {response.message}")

    def enable_callback(self, request, response):
        if request.data:
            pose = self.current_pose()
            if pose is None:
                response.success = False
                response.message = (
                    "map -> base_footprint transform unavailable"
                )
                return response
            self.start_pose = pose
            self.visited_goals.clear()
            self.inspection_queue.clear()
            self.inspection_prepared = False
            self.target_candidates.clear()
            self.target_collection_deadline = None
            self.confirmation_deadline = None
            self.stationary_ready_time = None
            self.target_selection_started = False
            self.path_candidates.clear()
            self.path_results.clear()
            self.active_target_position = None
            self.last_candidate_time = None
            self.mission.start()
            if self.grid is not None:
                self.mission.map_ready()
                self.request_motion(True)
                message = "map available; starting frontier exploration"
            else:
                message = "mapping and waiting for a usable map"
            response.success = True
            response.message = "search started"
            self.publish_status(message)
        else:
            self.cancel_navigation()
            self.target_selection_started = False
            self.target_collection_deadline = None
            self.confirmation_deadline = None
            self.stationary_ready_time = None
            self.target_candidates.clear()
            self.request_motion(False)
            self.mission.cancel()
            response.success = True
            response.message = "search cancelled"
            self.publish_status("cancelled by operator")
        return response

    def target_callback(self, message):
        if self.mission.state not in (
            SearchState.EXPLORING,
            SearchState.INSPECTING,
            SearchState.CONFIRMING,
            SearchState.APPROACHING,
        ):
            return
        if message.header.frame_id != "map" or self.grid is None:
            self.get_logger().warning("Ignoring target outside the map frame")
            return

        position = message.pose.pose.position
        orientation = message.pose.pose.orientation
        yaw = math.atan2(
            2.0 * orientation.w * orientation.z,
            1.0 - 2.0 * orientation.z * orientation.z,
        )
        target = np.array([position.x, position.y, position.z])
        free_normal = np.array([math.cos(yaw), math.sin(yaw)])
        now = self.get_clock().now().nanoseconds * 1e-9
        self.last_candidate_time = now

        if self.mission.state == SearchState.APPROACHING:
            if (
                self.active_target_position is not None
                and np.linalg.norm(
                    target - self.active_target_position
                ) <= 0.25
            ):
                return
            self.cancel_navigation()
            self.request_motion(False)
            state = self.mission.approach_failed()
            self.target_candidates.clear()
            self.target_selection_started = False
            self.target_collection_deadline = None
            self.confirmation_deadline = now + 2.0
            self.stationary_ready_time = now + 0.20
            self.reset_confirmation_window()
            self.publish_status("target moved; stopped for reconfirmation")
            if state == SearchState.EXPLORING:
                self.request_motion(True)
            return

        if self.mission.state in (
            SearchState.EXPLORING,
            SearchState.INSPECTING,
        ):
            self.mission.candidate_detected()
            self.cancel_navigation()
            self.request_motion(False)
            self.target_candidates.clear()
            self.target_collection_deadline = None
            self.target_selection_started = False
            self.confirmation_deadline = now + 2.0
            self.stationary_ready_time = now + 0.20
            self.reset_confirmation_window()
            self.publish_status(
                "confirmed candidate; restarting while stopped"
            )
            return

        if (
            self.stationary_ready_time is not None
            and now < self.stationary_ready_time
        ):
            return

        if not any(
            np.linalg.norm(candidate[0] - target) < 0.30
            for candidate in self.target_candidates
        ):
            self.target_candidates.append((target, free_normal))

        self.confirmation_deadline = None
        self.stationary_ready_time = None
        if (
            self.mission.state == SearchState.CONFIRMING
            and not self.target_selection_started
            and self.target_collection_deadline is None
        ):
            self.target_collection_deadline = now + 0.50

    def reset_confirmation_window(self):
        message = Bool()
        message.data = True
        self.confirmation_reset_pub.publish(message)

    def candidate_callback(self, message):
        if not message.data:
            return
        now = self.get_clock().now().nanoseconds * 1e-9
        self.last_candidate_time = now
        if self.mission.state in (
            SearchState.EXPLORING,
            SearchState.INSPECTING,
        ):
            self.mission.candidate_detected()
            self.cancel_navigation()
            self.request_motion(False)
            self.target_candidates.clear()
            self.target_collection_deadline = None
            self.target_selection_started = False
            self.confirmation_deadline = now + 2.0
            self.stationary_ready_time = now + 0.20
            self.reset_confirmation_window()
            self.publish_status("candidate detected; stopped for confirmation")
        elif (
            self.mission.state == SearchState.CONFIRMING
            and self.confirmation_deadline is None
            and self.target_collection_deadline is None
            and not self.target_selection_started
        ):
            self.confirmation_deadline = now + 2.0
            self.stationary_ready_time = now + 0.20
            self.reset_confirmation_window()

    def filter_unvisited(self, goals):
        separation = self.get_parameter("goal_separation").value
        return [
            goal for goal in goals
            if all(
                math.hypot(goal.x - visited.x, goal.y - visited.y)
                >= separation
                for visited in self.visited_goals
            )
        ]

    def nearest_goal(self, goals):
        current = self.current_pose()
        if current is None or not goals:
            return None
        return min(
            goals,
            key=lambda goal: math.hypot(
                goal.x - current.x,
                goal.y - current.y,
            ),
        )

    def dispatch_exploration(self):
        goals = frontier_goals(
            self.grid,
            min_cluster_cells=self.get_parameter("frontier_min_cells").value,
        )
        goals = self.filter_unvisited(goals)
        goal = self.nearest_goal(goals)
        if goal is None:
            state = self.mission.frontier_exhausted()
            if state == SearchState.INSPECTING:
                self.prepare_inspection()
            return
        self.mission.frontier_available()
        self.send_navigation_goal(goal, "frontier")

    def prepare_inspection(self):
        self.inspection_queue = inspection_goals(
            self.grid,
            spacing=self.get_parameter("inspection_spacing").value,
            robot_length=self.get_parameter("robot_length").value,
            robot_width=self.get_parameter("robot_width").value,
            inflation=self.get_parameter("safety_inflation").value,
        )
        self.inspection_queue = self.filter_unvisited(self.inspection_queue)
        self.inspection_prepared = True
        path = Path()
        path.header.stamp = self.get_clock().now().to_msg()
        path.header.frame_id = "map"
        path.poses = [
            self.pose_message(goal, path.header.stamp)
            for goal in self.inspection_queue
        ]
        self.inspection_path_pub.publish(path)
        self.publish_status(
            f"frontiers exhausted; {len(self.inspection_queue)} wall poses"
        )

    def dispatch_inspection(self):
        if not self.inspection_queue:
            self.mission.inspection_complete()
            if self.start_pose is None:
                self.fail("start pose unavailable for return")
                return
            self.send_navigation_goal(self.start_pose, "return")
            self.publish_status("inspection complete; returning to start")
            return
        goal = self.nearest_goal(self.inspection_queue)
        self.inspection_queue.remove(goal)
        self.send_navigation_goal(goal, "inspection")

    def send_navigation_goal(self, pose, purpose):
        if self.goal_handle is not None or self.goal_pending:
            return
        if not self.navigator.server_is_ready():
            self.get_logger().warning("Nav2 navigate_to_pose is unavailable")
            return
        goal = NavigateToPose.Goal()
        goal.pose = self.pose_message(pose, self.get_clock().now().to_msg())
        self.goal_purpose = purpose
        self.goal_pose = pose
        self.goal_pending = True
        self.navigation_generation += 1
        generation = self.navigation_generation
        future = self.navigator.send_goal_async(goal)
        future.add_done_callback(
            lambda result: self.goal_response(result, generation)
        )

    def goal_response(self, future, generation):
        try:
            handle = future.result()
        except Exception as error:
            if generation != self.navigation_generation:
                return
            self.goal_pending = False
            self.fail(f"Nav2 goal request failed: {error}")
            return
        if generation != self.navigation_generation:
            if handle.accepted:
                handle.cancel_goal_async()
            return
        self.goal_pending = False
        if not handle.accepted:
            self.goal_handle = None
            self.navigation_finished(False)
            return
        self.goal_handle = handle
        result_future = handle.get_result_async()
        result_future.add_done_callback(
            lambda result: self.goal_result(result, generation)
        )

    def goal_result(self, future, generation):
        if generation != self.navigation_generation:
            return
        try:
            result = future.result()
            succeeded = result.status == GoalStatus.STATUS_SUCCEEDED
        except Exception:
            succeeded = False
        self.goal_handle = None
        self.navigation_finished(succeeded)

    def navigation_finished(self, succeeded):
        purpose = self.goal_purpose
        goal_pose = self.goal_pose
        self.goal_purpose = None
        self.goal_pose = None
        if purpose in ("frontier", "inspection") and goal_pose is not None:
            self.visited_goals.append(goal_pose)

        if purpose == "approach":
            if succeeded:
                self.mission.target_reached()
                self.request_motion(False)
                self.publish_status("target reached and robot stopped")
            else:
                state = self.mission.approach_failed()
                self.request_motion(False)
                self.publish_status(f"approach failed; state={state.value}")
        elif purpose == "return" and succeeded:
            self.mission.returned_without_target()
            self.request_motion(False)
            self.publish_status("returned to start; target not found")
        elif purpose == "return":
            self.fail("unable to return to the recorded start pose")
        elif purpose == "inspection" and succeeded:
            self.start_inspection_scan()
        elif not succeeded:
            self.publish_status(f"navigation failed: {purpose}")

    def cancel_navigation(self):
        self.navigation_generation += 1
        if self.goal_handle is not None:
            self.goal_handle.cancel_goal_async()
        self.goal_handle = None
        self.goal_pending = False
        self.goal_purpose = None
        self.goal_pose = None
        if self.scan_handle is not None:
            self.scan_handle.cancel_goal_async()
        self.scan_generation += 1
        self.scan_handle = None
        self.scan_pending = False

    def start_inspection_scan(self):
        """Rotate once at an inspection pose so the detector sees the wall."""
        if not self.spinner.server_is_ready():
            self.get_logger().warning("Nav2 spin action is unavailable")
            return
        goal = Spin.Goal()
        goal.target_yaw = 2.0 * math.pi
        goal.time_allowance.sec = 30
        self.scan_pending = True
        self.scan_generation += 1
        generation = self.scan_generation
        future = self.spinner.send_goal_async(goal)
        future.add_done_callback(
            lambda result: self.scan_goal_response(result, generation)
        )

    def scan_goal_response(self, future, generation):
        if generation != self.scan_generation:
            try:
                handle = future.result()
                if handle.accepted:
                    handle.cancel_goal_async()
            except Exception:
                pass
            return
        self.scan_pending = False
        try:
            handle = future.result()
        except Exception as error:
            self.get_logger().warning(f"inspection scan failed: {error}")
            return
        if not handle.accepted:
            self.get_logger().warning("inspection scan was rejected")
            return
        self.scan_handle = handle
        result_future = handle.get_result_async()
        result_future.add_done_callback(
            lambda result: self.scan_goal_result(result, generation)
        )

    def scan_goal_result(self, future, generation):
        if generation != self.scan_generation:
            return
        try:
            wrapped = future.result()
            if wrapped.status != GoalStatus.STATUS_SUCCEEDED:
                self.get_logger().warning("inspection scan did not complete")
        except Exception as error:
            self.get_logger().warning(f"inspection scan failed: {error}")
        self.scan_handle = None

    @staticmethod
    def path_length(path):
        total = 0.0
        for first, second in zip(path.poses, path.poses[1:]):
            total += math.hypot(
                second.pose.position.x - first.pose.position.x,
                second.pose.position.y - first.pose.position.y,
            )
        return total

    def begin_target_selection(self):
        """Create safe poses and rank reachable ones using Nav2 paths."""
        self.target_selection_started = True
        self.path_candidates.clear()
        self.path_results.clear()
        parameter = self.get_parameter
        for target, free_normal in self.target_candidates:
            standoff = nearest_safe_standoff(
                self.grid,
                target,
                free_normal,
                robot_length=parameter("robot_length").value,
                robot_width=parameter("robot_width").value,
                inflation=parameter("safety_inflation").value,
            )
            if standoff is not None:
                self.path_candidates.append((standoff, target))
        if not self.path_candidates:
            self.target_selection_failed("no collision-free target pose")
            return
        if not self.path_planner.server_is_ready():
            self.target_selection_failed("Nav2 path planner unavailable")
            return
        self.plan_next_target_path()

    def plan_next_target_path(self):
        if not self.path_candidates:
            if not self.path_results:
                self.target_selection_failed("no target pose has a valid path")
                return
            _, best_pose, best_target = min(
                self.path_results,
                key=lambda item: item[0],
            )
            self.target_candidates.clear()
            self.target_collection_deadline = None
            self.confirmation_deadline = None
            self.stationary_ready_time = None
            self.target_selection_started = False
            self.active_target_position = best_target
            self.mission.target_confirmed()
            self.request_motion(True)
            self.send_navigation_goal(best_pose, "approach")
            self.publish_status("approaching target with shortest Nav2 path")
            return

        pose, target = self.path_candidates.pop(0)
        goal = ComputePathToPose.Goal()
        goal.goal = self.pose_message(pose, self.get_clock().now().to_msg())
        goal.use_start = False
        future = self.path_planner.send_goal_async(goal)
        future.add_done_callback(
            lambda result: self.path_goal_response(result, pose, target)
        )

    def path_goal_response(self, future, pose, target):
        if (
            self.mission.state != SearchState.CONFIRMING
            or not self.target_selection_started
        ):
            return
        try:
            handle = future.result()
        except Exception as error:
            self.get_logger().warning(f"path request failed: {error}")
            self.plan_next_target_path()
            return
        if not handle.accepted:
            self.plan_next_target_path()
            return
        result_future = handle.get_result_async()
        result_future.add_done_callback(
            lambda result: self.path_goal_result(result, pose, target)
        )

    def path_goal_result(self, future, pose, target):
        if (
            self.mission.state != SearchState.CONFIRMING
            or not self.target_selection_started
        ):
            return
        try:
            wrapped = future.result()
            if wrapped.status == GoalStatus.STATUS_SUCCEEDED:
                length = self.path_length(wrapped.result.path)
                if wrapped.result.path.poses:
                    self.path_results.append((length, pose, target))
        except Exception as error:
            self.get_logger().warning(f"path result failed: {error}")
        self.plan_next_target_path()

    def target_selection_failed(self, reason):
        self.target_candidates.clear()
        self.target_collection_deadline = None
        self.confirmation_deadline = None
        self.stationary_ready_time = None
        self.target_selection_started = False
        state = self.mission.approach_failed()
        if state == SearchState.EXPLORING:
            self.request_motion(True)
        self.publish_status(f"{reason}; state={state.value}")

    def fail(self, reason):
        self.cancel_navigation()
        self.target_selection_started = False
        self.target_candidates.clear()
        self.confirmation_deadline = None
        self.stationary_ready_time = None
        self.request_motion(False)
        self.mission.fault()
        self.publish_status(reason, DiagnosticStatus.ERROR)

    def publish_status(self, message, level=DiagnosticStatus.OK):
        report = DiagnosticArray()
        report.header.stamp = self.get_clock().now().to_msg()
        status = DiagnosticStatus()
        status.level = level
        status.name = "go2_scissors_search"
        status.hardware_id = "unitree_go2"
        status.message = self.mission.state.value
        status.values = [
            KeyValue(key="detail", value=str(message)),
            KeyValue(key="state", value=self.mission.state.value),
        ]
        report.status = [status]
        self.status_pub.publish(report)
        self.last_state = self.mission.state

    def tick(self):
        if self.grid is None:
            return
        now = self.get_clock().now().nanoseconds * 1e-9
        if (
            self.mission.state == SearchState.APPROACHING
            and self.last_candidate_time is not None
            and now - self.last_candidate_time > 1.0
        ):
            self.cancel_navigation()
            self.request_motion(False)
            state = self.mission.approach_failed()
            self.confirmation_deadline = None
            self.stationary_ready_time = None
            self.target_collection_deadline = None
            self.target_selection_started = False
            self.publish_status(f"target lost during approach; {state.value}")
            return
        if (
            self.goal_handle is not None
            or self.goal_pending
            or self.scan_handle is not None
            or self.scan_pending
        ):
            return
        if self.mission.state == SearchState.EXPLORING:
            self.dispatch_exploration()
        elif self.mission.state == SearchState.INSPECTING:
            if not self.inspection_prepared:
                self.prepare_inspection()
            self.dispatch_inspection()
        elif (
            self.mission.state == SearchState.CONFIRMING
            and not self.target_selection_started
            and self.target_collection_deadline is not None
            and now >= self.target_collection_deadline
        ):
            self.begin_target_selection()
        elif (
            self.mission.state == SearchState.CONFIRMING
            and self.confirmation_deadline is not None
            and now >= self.confirmation_deadline
        ):
            self.confirmation_deadline = None
            self.stationary_ready_time = None
            state = self.mission.approach_failed()
            if state == SearchState.EXPLORING:
                self.request_motion(True)
            self.publish_status(
                f"target confirmation timed out; {state.value}"
            )


def main(args=None):
    rclpy.init(args=args)
    node = MissionManagerNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.cancel_navigation()
        node.request_motion(False)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
