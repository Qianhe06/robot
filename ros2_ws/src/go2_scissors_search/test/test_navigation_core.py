"""Tests for exploration, inspection, standoff, and mission state logic."""

import math

import numpy as np

from go2_scissors_search.mission_core import SearchMission, SearchState
from go2_scissors_search.navigation_core import frontier_goals
from go2_scissors_search.navigation_core import cluster_target_positions
from go2_scissors_search.navigation_core import CostGrid
from go2_scissors_search.navigation_core import GridMap
from go2_scissors_search.navigation_core import inspection_goals
from go2_scissors_search.navigation_core import nearest_safe_standoff
from go2_scissors_search.navigation_core import pose_is_safe
from go2_scissors_search.navigation_core import Pose2D


def test_frontier_cells_form_one_representative_goal():
    data = np.full((40, 40), -1, dtype=np.int16)
    data[5:35, 5:35] = 0
    grid = GridMap(data, 40, 40, 0.1)

    goals = frontier_goals(grid, min_cluster_cells=4)

    assert len(goals) == 1
    assert pose_is_safe(grid, goals[0])


def test_frontier_goal_is_moved_inward_from_unknown_space():
    data = np.full((50, 50), -1, dtype=np.int16)
    data[5:45, 5:45] = 0
    grid = GridMap(data, 50, 50, 0.1)

    goal = frontier_goals(grid, min_cluster_cells=4)[0]
    row, column = grid.world_to_cell(goal.x, goal.y)

    assert 10 <= row <= 39
    assert 10 <= column <= 39
    assert pose_is_safe(grid, goal)


def test_cost_grid_rejects_inscribed_lethal_unknown_and_outside():
    costs = np.array([[0, 252, 253, 254, 255]], dtype=np.int16)
    grid = CostGrid(costs, 5, 1, 1.0)

    assert grid.pose_is_traversable(Pose2D(0.5, 0.5, 0.0))
    assert grid.pose_is_traversable(Pose2D(1.5, 0.5, 0.0))
    assert not grid.pose_is_traversable(Pose2D(2.5, 0.5, 0.0))
    assert not grid.pose_is_traversable(Pose2D(3.5, 0.5, 0.0))
    assert not grid.pose_is_traversable(Pose2D(4.5, 0.5, 0.0))
    assert not grid.pose_is_traversable(Pose2D(5.5, 0.5, 0.0))


def test_inspection_goals_face_obstacle_and_respect_spacing():
    data = np.zeros((40, 40), dtype=np.int16)
    data[:, 20] = 100
    grid = GridMap(data, 40, 40, 0.1)

    goals = inspection_goals(grid, spacing=0.5)

    assert len(goals) >= 3
    for goal in goals:
        direction_to_wall = 2.05 - goal.x
        assert direction_to_wall * math.cos(goal.yaw) > 0.0
        assert pose_is_safe(grid, goal)
    for first, second in zip(goals, goals[1:]):
        assert math.hypot(first.x - second.x, first.y - second.y) >= 0.5


def test_standoff_moves_away_until_footprint_is_safe():
    data = np.zeros((80, 80), dtype=np.int16)
    data[:, 40] = 100
    grid = GridMap(data, 80, 80, 0.05, origin_x=-2.0, origin_y=-2.0)

    pose = nearest_safe_standoff(
        grid,
        target=[0.0, 0.0],
        free_space_normal=[-1.0, 0.0],
    )

    assert pose is not None
    assert pose.x <= -0.55 + 1e-9
    assert pose_is_safe(grid, pose)
    assert pose.yaw == 0.0


def test_standoff_rejects_unreachable_free_pose():
    data = np.zeros((40, 40), dtype=np.int16)
    data[:, 20] = 100
    grid = GridMap(data, 40, 40, 0.1, origin_x=-2.0, origin_y=-2.0)
    reachable = np.zeros_like(data, dtype=bool)

    pose = nearest_safe_standoff(
        grid,
        target=[0.0, 0.0],
        free_space_normal=[-1.0, 0.0],
        reachable_mask=reachable,
    )

    assert pose is None


def test_nearby_target_positions_form_separate_weighted_clusters():
    clusters = cluster_target_positions([
        [1.00, 0.00, 1.2],
        [1.08, 0.02, 1.2],
        [3.00, 1.00, 1.1],
    ], radius=0.20)

    assert sorted(count for _, count in clusters) == [1, 2]
    large_center = next(center for center, count in clusters if count == 2)
    np.testing.assert_allclose(large_center, [1.04, 0.01, 1.2])


def test_mission_falls_back_to_inspection_and_returns_home():
    mission = SearchMission(frontier_exhaustion_limit=3)

    assert mission.start() == SearchState.MAPPING
    assert mission.map_ready() == SearchState.EXPLORING
    assert mission.frontier_exhausted() == SearchState.EXPLORING
    assert mission.frontier_exhausted() == SearchState.EXPLORING
    assert mission.frontier_exhausted() == SearchState.INSPECTING
    assert mission.inspection_complete() == SearchState.RETURNING
    assert mission.returned_without_target() == SearchState.NOT_FOUND


def test_mission_reconfirms_then_resumes_search_after_failures():
    mission = SearchMission(retry_limit=2)
    mission.start()
    mission.map_ready()
    mission.candidate_detected()
    mission.target_confirmed()

    assert mission.approach_failed() == SearchState.CONFIRMING
    assert mission.approach_failed() == SearchState.EXPLORING
