#!/usr/bin/env python3
"""Summarize the live map, global costmap, and exploration goals."""

from collections import Counter
import math
import sys
import time

from nav2_msgs.msg import Costmap
from nav_msgs.msg import OccupancyGrid
from sensor_msgs.msg import LaserScan
import numpy as np
import rclpy
from rcl_interfaces.srv import GetParameters
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, qos_profile_sensor_data
from rclpy.time import Time
from tf2_ros import Buffer, TransformException, TransformListener

from go2_scissors_search.navigation_core import CostGrid, frontier_cells
from go2_scissors_search.navigation_core import frontier_goals, GridMap
from go2_scissors_search.navigation_core import inspection_goals
from go2_scissors_search.navigation_core import pose_is_safe, Pose2D


class ExplorationDiagnostic(Node):
    def __init__(self):
        super().__init__("go2_exploration_diagnostic")
        self.map_message = None
        self.costmap_message = None
        self.scan_message = None
        self.map_count = 0
        self.map_changed_count = 0
        self.scan_count = 0
        map_qos = QoSProfile(depth=1)
        map_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
        self.create_subscription(
            OccupancyGrid, "/map", self.map_callback, map_qos
        )
        self.create_subscription(
            Costmap, "/global_costmap/costmap_raw",
            self.costmap_callback, 10,
        )
        self.create_subscription(
            LaserScan, "/scan", self.scan_callback,
            qos_profile_sensor_data,
        )
        self.parameters_client = self.create_client(
            GetParameters,
            "/scissors_search_manager/get_parameters",
        )
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)

    def map_callback(self, message):
        previous = self.map_message
        old_origin = previous.info.origin.position if previous else None
        new_origin = message.info.origin.position
        if previous is not None and (
            previous.info.width != message.info.width
            or previous.info.height != message.info.height
            or old_origin.x != new_origin.x
            or old_origin.y != new_origin.y
            or previous.data != message.data
        ):
            self.map_changed_count += 1
        self.map_message = message
        self.map_count += 1

    def costmap_callback(self, message):
        self.costmap_message = message

    def scan_callback(self, message):
        self.scan_message = message
        self.scan_count += 1

    def wait_for(self, ready, description, timeout=10.0):
        deadline = time.monotonic() + timeout
        while rclpy.ok() and not ready() and time.monotonic() < deadline:
            rclpy.spin_once(self, timeout_sec=0.1)
        if not ready():
            raise RuntimeError(f"{description} unavailable")

    def parameters(self):
        names = [
            "robot_length", "robot_width", "safety_inflation",
            "frontier_min_cells", "inspection_spacing",
        ]
        self.wait_for(
            self.parameters_client.service_is_ready,
            "mission manager parameters",
        )
        request = GetParameters.Request()
        request.names = names
        future = self.parameters_client.call_async(request)
        self.wait_for(future.done, "mission manager parameter response")
        values = future.result().values
        return {
            name: value.integer_value if name == "frontier_min_cells"
            else value.double_value
            for name, value in zip(names, values)
        }

    def robot_pose(self):
        deadline = time.monotonic() + 5.0
        while rclpy.ok() and time.monotonic() < deadline:
            try:
                transform = self.tf_buffer.lookup_transform(
                    "map", "base_footprint", Time(),
                    timeout=Duration(seconds=0.1),
                )
                translation = transform.transform.translation
                rotation = transform.transform.rotation
                yaw = math.atan2(
                    2.0 * (rotation.w * rotation.z
                           + rotation.x * rotation.y),
                    1.0 - 2.0 * (rotation.y * rotation.y
                                 + rotation.z * rotation.z),
                )
                return Pose2D(translation.x, translation.y, yaw)
            except TransformException:
                rclpy.spin_once(self, timeout_sec=0.1)
        raise RuntimeError("map -> base_footprint TF unavailable")

    def report(self):
        self.wait_for(
            lambda: self.map_message is not None
            and self.costmap_message is not None,
            "map or global costmap",
        )
        params = self.parameters()
        pose = self.robot_pose()
        map_msg = self.map_message
        map_info = map_msg.info
        map_origin = map_info.origin.position
        grid = GridMap(
            map_msg.data, map_info.width, map_info.height,
            map_info.resolution, map_origin.x, map_origin.y,
        )
        cost_msg = self.costmap_message
        cost_info = cost_msg.metadata
        cost_origin = cost_info.origin.position
        cost_grid = CostGrid(
            cost_msg.data, cost_info.size_x, cost_info.size_y,
            cost_info.resolution, cost_origin.x, cost_origin.y,
        )
        values = grid.data
        free = int(np.count_nonzero((values >= 0) & (values < 25)))
        occupied = int(np.count_nonzero(values >= 65))
        unknown = int(np.count_nonzero(values < 0))
        print(
            f"map={grid.width}x{grid.height}, "
            f"resolution={grid.resolution:.3f} m, "
            f"free={free}, occupied={occupied}, unknown={unknown}"
        )
        print(
            f"manager: footprint={params['robot_length']:.2f}x"
            f"{params['robot_width']:.2f} m, "
            f"extra_margin={params['safety_inflation']:.2f} m, "
            f"frontier_min_cells={params['frontier_min_cells']}"
        )
        cell = grid.world_to_cell(pose.x, pose.y)
        map_value = None if cell is None else int(values[cell])
        robot_cost = cost_grid.cost_at(pose.x, pose.y)
        robot_safe = pose_is_safe(
            grid, pose,
            robot_length=params["robot_length"],
            robot_width=params["robot_width"],
            inflation=params["safety_inflation"],
        )
        print(
            f"robot=({pose.x:.3f}, {pose.y:.3f}, "
            f"{math.degrees(pose.yaw):.1f} deg), "
            f"map={map_value}, global_cost={robot_cost}, "
            f"footprint_safe={robot_safe}"
        )
        frontiers = frontier_cells(grid)
        goals = frontier_goals(
            grid,
            min_cluster_cells=params["frontier_min_cells"],
            robot_length=params["robot_length"],
            robot_width=params["robot_width"],
            inflation=params["safety_inflation"],
        )
        costs = [cost_grid.cost_at(goal.x, goal.y) for goal in goals]
        traversable = sum(cost is not None and cost < 253 for cost in costs)
        print(
            f"frontier_cells={len(frontiers)}, "
            f"mapped_safe_goals={len(goals)}, "
            f"costmap_traversable_goals={traversable}, "
            f"goal_costs={dict(Counter(costs))}"
        )
        for goal, cost in zip(goals[:8], costs[:8]):
            print(f"  goal=({goal.x:.3f}, {goal.y:.3f}), cost={cost}")
        inspection = inspection_goals(
            grid,
            spacing=params["inspection_spacing"],
            robot_length=params["robot_length"],
            robot_width=params["robot_width"],
            inflation=params["safety_inflation"],
        )
        print(f"mapped_safe_inspection_poses={len(inspection)}")

    def watch(self, duration=10.0):
        self.wait_for(
            lambda: self.map_message is not None
            and self.scan_message is not None,
            "map or scan",
        )
        start_pose = self.robot_pose()
        start_maps = self.map_count
        start_changes = self.map_changed_count
        start_scans = self.scan_count
        deadline = time.monotonic() + duration
        print(f"观察 {duration:.0f} 秒：请让机器狗正常行走。", flush=True)
        while rclpy.ok() and time.monotonic() < deadline:
            rclpy.spin_once(self, timeout_sec=0.1)
        end_pose = self.robot_pose()
        distance = math.hypot(
            end_pose.x - start_pose.x,
            end_pose.y - start_pose.y,
        )
        yaw_change = math.atan2(
            math.sin(end_pose.yaw - start_pose.yaw),
            math.cos(end_pose.yaw - start_pose.yaw),
        )
        finite = sum(
            math.isfinite(value) for value in self.scan_message.ranges
        )
        print(
            f"/scan: {self.scan_count - start_scans} 帧，"
            f"最后一帧有效测距 {finite}/{len(self.scan_message.ranges)}"
        )
        print(
            f"map->base_footprint: 位移 {distance:.3f} 米，"
            f"转角 {math.degrees(yaw_change):.1f} 度"
        )
        print(
            f"/map: {self.map_count - start_maps} 帧，"
            f"内容变化 {self.map_changed_count - start_changes} 次"
        )


def main():
    if len(sys.argv) > 2 or (len(sys.argv) == 2 and sys.argv[1] != "--watch"):
        raise SystemExit("用法: diagnose_exploration.py [--watch]")
    rclpy.init(args=[])
    node = ExplorationDiagnostic()
    try:
        if len(sys.argv) == 2:
            node.watch()
        else:
            node.report()
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
