"""Grid-based exploration, inspection, and safe standoff planning."""

from collections import deque
from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class Pose2D:
    """A planar pose in a map frame."""

    x: float
    y: float
    yaw: float


class GridMap:
    """A validated occupancy grid with ROS map coordinate conversion."""

    def __init__(
        self,
        data,
        width,
        height,
        resolution,
        origin_x=0.0,
        origin_y=0.0,
    ):
        self.width = int(width)
        self.height = int(height)
        self.resolution = float(resolution)
        self.origin_x = float(origin_x)
        self.origin_y = float(origin_y)
        values = np.asarray(data, dtype=np.int16)
        if self.width < 1 or self.height < 1 or self.resolution <= 0.0:
            raise ValueError("invalid occupancy grid metadata")
        if values.size != self.width * self.height:
            raise ValueError("occupancy data size does not match dimensions")
        self.data = values.reshape(self.height, self.width)

    def cell_to_world(self, row, column):
        """Return the world coordinates of a grid-cell centre."""
        return np.array([
            self.origin_x + (int(column) + 0.5) * self.resolution,
            self.origin_y + (int(row) + 0.5) * self.resolution,
        ])

    def world_to_cell(self, x, y):
        """Return ``(row, column)`` or ``None`` when outside the grid."""
        column = math.floor((float(x) - self.origin_x) / self.resolution)
        row = math.floor((float(y) - self.origin_y) / self.resolution)
        if 0 <= row < self.height and 0 <= column < self.width:
            return row, column
        return None

    def is_free(self, row, column, free_threshold=25):
        """Return whether a cell is known and below the occupancy threshold."""
        return 0 <= self.data[row, column] < free_threshold


class CostGrid(GridMap):
    """A Nav2 cost grid with point-cost safety queries."""

    def cost_at(self, x, y):
        """Return the cost at a world coordinate or ``None`` outside."""
        cell = self.world_to_cell(x, y)
        if cell is None:
            return None
        return int(self.data[cell])

    def pose_is_traversable(self, pose, lethal_threshold=253):
        """Reject inscribed, lethal, unknown, and out-of-grid poses."""
        cost = self.cost_at(pose.x, pose.y)
        return cost is not None and cost < int(lethal_threshold)


def _neighbours(row, column, height, width, diagonals=False):
    offsets = [(-1, 0), (1, 0), (0, -1), (0, 1)]
    if diagonals:
        offsets += [(-1, -1), (-1, 1), (1, -1), (1, 1)]
    for row_offset, column_offset in offsets:
        other_row = row + row_offset
        other_column = column + column_offset
        if 0 <= other_row < height and 0 <= other_column < width:
            yield other_row, other_column


def frontier_cells(grid, free_threshold=25):
    """Return free cells sharing an edge with unknown map space."""
    result = []
    for row in range(grid.height):
        for column in range(grid.width):
            if not grid.is_free(row, column, free_threshold):
                continue
            if any(
                grid.data[other] < 0
                for other in _neighbours(
                    row,
                    column,
                    grid.height,
                    grid.width,
                )
            ):
                result.append((row, column))
    return result


def _connected_components(cells, height, width):
    remaining = set(cells)
    components = []
    while remaining:
        start = remaining.pop()
        queue = deque([start])
        component = [start]
        while queue:
            row, column = queue.popleft()
            for neighbour in _neighbours(
                row,
                column,
                height,
                width,
                diagonals=True,
            ):
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    queue.append(neighbour)
                    component.append(neighbour)
        components.append(component)
    return components


def _safe_frontier_pose(
    grid,
    frontier_row,
    frontier_column,
    frontier_yaw,
    free_threshold,
    robot_length,
    robot_width,
    inflation,
    max_standoff,
):
    """Move a frontier target inward until the full footprint is safe."""
    frontier_x, frontier_y = grid.cell_to_world(
        frontier_row,
        frontier_column,
    )
    search_cells = math.ceil(float(max_standoff) / grid.resolution)
    candidates = []
    for row in range(
        max(0, frontier_row - search_cells),
        min(grid.height, frontier_row + search_cells + 1),
    ):
        for column in range(
            max(0, frontier_column - search_cells),
            min(grid.width, frontier_column + search_cells + 1),
        ):
            if not grid.is_free(row, column, free_threshold):
                continue
            distance = math.hypot(
                row - frontier_row,
                column - frontier_column,
            )
            candidates.append((distance, row, column))

    for _, row, column in sorted(candidates):
        x, y = grid.cell_to_world(row, column)
        if row == frontier_row and column == frontier_column:
            yaw = frontier_yaw
        else:
            yaw = math.atan2(frontier_y - y, frontier_x - x)
        pose = Pose2D(float(x), float(y), yaw)
        if pose_is_safe(
            grid,
            pose,
            robot_length=robot_length,
            robot_width=robot_width,
            inflation=inflation,
            free_threshold=free_threshold,
        ):
            return pose
    return None


def frontier_goals(
    grid,
    min_cluster_cells=5,
    free_threshold=25,
    robot_length=0.80,
    robot_width=0.52,
    inflation=0.15,
    max_standoff=2.0,
):
    """Return footprint-safe poses facing each frontier cluster."""
    cells = frontier_cells(grid, free_threshold)
    goals = []
    for component in _connected_components(cells, grid.height, grid.width):
        if len(component) < min_cluster_cells:
            continue
        center = np.mean(component, axis=0)
        row, column = min(
            component,
            key=lambda cell: float(np.linalg.norm(np.asarray(cell) - center)),
        )
        unknown = [
            cell for cell in _neighbours(
                row,
                column,
                grid.height,
                grid.width,
                diagonals=True,
            )
            if grid.data[cell] < 0
        ]
        yaw = 0.0
        if unknown:
            unknown_center = np.mean(unknown, axis=0)
            yaw = math.atan2(
                unknown_center[0] - row,
                unknown_center[1] - column,
            )
        pose = _safe_frontier_pose(
            grid,
            row,
            column,
            yaw,
            free_threshold,
            robot_length,
            robot_width,
            inflation,
            max_standoff,
        )
        if pose is not None:
            goals.append(pose)
    return goals


def inspection_goals(
    grid,
    spacing=0.80,
    free_threshold=25,
    occupied_threshold=65,
    robot_length=0.80,
    robot_width=0.52,
    inflation=0.15,
):
    """Sample free poses along mapped obstacle boundaries facing the wall."""
    candidates = []
    for row in range(grid.height):
        for column in range(grid.width):
            if not grid.is_free(row, column, free_threshold):
                continue
            occupied = [
                cell for cell in _neighbours(
                    row,
                    column,
                    grid.height,
                    grid.width,
                    diagonals=True,
                )
                if grid.data[cell] >= occupied_threshold
            ]
            if occupied:
                obstacle_center = np.mean(occupied, axis=0)
                target_x, target_y = grid.cell_to_world(
                    obstacle_center[0],
                    obstacle_center[1],
                )
                free_normal = np.array([
                    column - obstacle_center[1],
                    row - obstacle_center[0],
                ])
                pose = nearest_safe_standoff(
                    grid,
                    [target_x, target_y],
                    free_normal,
                    max_distance=1.5,
                    robot_length=robot_length,
                    robot_width=robot_width,
                    inflation=inflation,
                )
                if pose is not None:
                    candidates.append(pose)

    selected = []
    minimum_spacing = float(spacing)
    for candidate in candidates:
        if all(
            math.hypot(candidate.x - goal.x, candidate.y - goal.y)
            >= minimum_spacing
            for goal in selected
        ):
            selected.append(candidate)
    return selected


def pose_is_safe(
    grid,
    pose,
    robot_length=0.80,
    robot_width=0.52,
    inflation=0.15,
    free_threshold=25,
):
    """Check a rotated rectangular footprint against known free cells."""
    half_length = 0.5 * float(robot_length) + float(inflation)
    half_width = 0.5 * float(robot_width) + float(inflation)
    radius = math.hypot(half_length, half_width)
    row_range = math.ceil(radius / grid.resolution)
    center = grid.world_to_cell(pose.x, pose.y)
    if center is None:
        return False

    cosine = math.cos(pose.yaw)
    sine = math.sin(pose.yaw)
    center_row, center_column = center
    for row in range(center_row - row_range, center_row + row_range + 1):
        for column in range(
            center_column - row_range,
            center_column + row_range + 1,
        ):
            if not (0 <= row < grid.height and 0 <= column < grid.width):
                return False
            x, y = grid.cell_to_world(row, column)
            delta_x = x - pose.x
            delta_y = y - pose.y
            local_x = cosine * delta_x + sine * delta_y
            local_y = -sine * delta_x + cosine * delta_y
            if abs(local_x) <= half_length and abs(local_y) <= half_width:
                if not grid.is_free(row, column, free_threshold):
                    return False
    return True


def nearest_safe_standoff(
    grid,
    target,
    free_space_normal,
    min_distance=0.35,
    max_distance=2.0,
    step=0.05,
    reachable_mask=None,
    robot_length=0.80,
    robot_width=0.52,
    inflation=0.15,
):
    """Find the closest collision-free pose on the wall's free-space side."""
    target = np.asarray(target, dtype=np.float64)
    normal = np.asarray(free_space_normal, dtype=np.float64)
    if target.shape not in ((2,), (3,)) or not np.isfinite(target).all():
        raise ValueError("target must contain two or three finite values")
    if normal.shape not in ((2,), (3,)) or not np.isfinite(normal).all():
        raise ValueError("free_space_normal must contain finite values")
    normal = normal[:2]
    norm = np.linalg.norm(normal)
    if norm <= 1e-10:
        raise ValueError("free_space_normal must be non-zero")
    normal /= norm
    target = target[:2]

    for distance in np.arange(
        float(min_distance),
        float(max_distance) + 0.5 * float(step),
        float(step),
    ):
        position = target + normal * distance
        yaw = math.atan2(target[1] - position[1], target[0] - position[0])
        pose = Pose2D(float(position[0]), float(position[1]), yaw)
        cell = grid.world_to_cell(pose.x, pose.y)
        if cell is None:
            continue
        if reachable_mask is not None and not bool(reachable_mask[cell]):
            continue
        if pose_is_safe(
            grid,
            pose,
            robot_length=robot_length,
            robot_width=robot_width,
            inflation=inflation,
        ):
            return pose
    return None


def cluster_target_positions(positions, radius=0.30):
    """Merge nearby target estimates into centres and sample counts."""
    pending = [
        np.asarray(position, dtype=np.float64) for position in positions
    ]
    clusters = []
    while pending:
        cluster = [pending.pop(0)]
        changed = True
        while changed:
            changed = False
            center = np.mean(cluster, axis=0)
            for index in range(len(pending) - 1, -1, -1):
                if np.linalg.norm(pending[index] - center) <= radius:
                    cluster.append(pending.pop(index))
                    changed = True
        clusters.append((np.mean(cluster, axis=0), len(cluster)))
    return clusters
