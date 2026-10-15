#!/usr/bin/env python3
"""Summarize the live map, global costmap, and exploration goals."""

from collections import Counter
import math
import time

from nav2_msgs.msg import Costmap
from nav_msgs.msg import OccupancyGrid
import numpy as np
import rclpy
from rcl_interfaces.srv import GetParameters
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
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
        map_qos = QoSProfile(depth=1)
        map_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
        self.create_subscription(
            OccupancyGrid, "/map", self.map_callback, map_qos
        )
        self.create_subscription(
            Costmap, "/global_costmap/costmap_raw",
            self.costmap_callback, 10,
        )
        self.parameters_client = self.create_client(
            GetParameters,
            "/scissors_search_manager/get_parameters",
        )
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)

    def map_callback(self, message):
        self.map_message = message

    def costmap_callback(self, message):
        self.costmap_message = message

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


def main():
    rclpy.init()
    node = ExplorationDiagnostic()
    try:
        node.report()
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
