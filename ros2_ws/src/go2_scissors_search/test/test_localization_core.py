"""Tests for hardware-independent target localization logic."""

import numpy as np
import pytest

from go2_scissors_search.localization_core import fit_vertical_plane_ransac
from go2_scissors_search.localization_core import intersect_ray_with_plane
from go2_scissors_search.localization_core import orient_wall_normal
from go2_scissors_search.localization_core import pixel_to_camera_ray
from go2_scissors_search.localization_core import project_pixel_to_plane
from go2_scissors_search.localization_core import TemporalTargetConfirmer


def test_center_pixel_projects_to_wall_plane():
    camera_matrix = np.array([
        [500.0, 0.0, 320.0],
        [0.0, 500.0, 240.0],
        [0.0, 0.0, 1.0],
    ])

    ray = pixel_to_camera_ray((320.0, 240.0), camera_matrix)
    point = project_pixel_to_plane(
        (320.0, 240.0),
        camera_matrix,
        camera_origin=np.zeros(3),
        camera_rotation=np.eye(3),
        plane_normal=np.array([0.0, 0.0, 1.0]),
        plane_offset=-2.0,
    )

    np.testing.assert_allclose(ray, [0.0, 0.0, 1.0], atol=1e-12)
    np.testing.assert_allclose(point, [0.0, 0.0, 2.0], atol=1e-12)


def test_ray_plane_intersection_rejects_parallel_and_behind_camera():
    parallel = intersect_ray_with_plane(
        [0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [1.0, 0.0, 0.0],
        -1.0,
    )
    behind = intersect_ray_with_plane(
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        1.0,
    )

    assert parallel is None
    assert behind is None


def test_wall_normal_points_toward_observer():
    normal = orient_wall_normal(
        plane_normal=[1.0, 0.0, 0.0],
        plane_point=[2.0, 0.0, 1.0],
        observer_position=[0.0, 0.0, 0.0],
    )

    np.testing.assert_allclose(normal, [-1.0, 0.0, 0.0])


def test_vertical_plane_ransac_ignores_outliers():
    generator = np.random.default_rng(7)
    wall = np.column_stack((
        2.0 + generator.normal(0.0, 0.005, 120),
        generator.uniform(-1.5, 1.5, 120),
        generator.uniform(0.1, 2.0, 120),
    ))
    outliers = generator.uniform(-3.0, 3.0, size=(30, 3))
    points = np.vstack((wall, outliers))

    fit = fit_vertical_plane_ransac(
        points,
        distance_threshold=0.025,
        min_inliers=100,
        random_seed=9,
    )

    assert len(fit.inlier_indices) >= 115
    assert abs(float(np.dot(fit.normal, [1.0, 0.0, 0.0]))) > 0.999
    assert fit.rms_error < 0.01


def test_vertical_plane_ransac_rejects_floor():
    generator = np.random.default_rng(4)
    floor = np.column_stack((
        generator.uniform(-2.0, 2.0, 80),
        generator.uniform(-2.0, 2.0, 80),
        generator.normal(0.0, 0.002, 80),
    ))

    with pytest.raises(ValueError, match="vertical plane"):
        fit_vertical_plane_ransac(
            floor,
            distance_threshold=0.02,
            min_inliers=60,
            random_seed=5,
        )


def test_temporal_confirmation_requires_quality_and_stability():
    confirmer = TemporalTargetConfirmer()

    assert confirmer.add([1.0, 0.0, 1.2], 10, 0.01) is None
    assert confirmer.add([1.00, 0.00, 1.20], 24, 0.02) is None
    assert confirmer.add([1.02, -0.01, 1.19], 30, 0.03) is None
    result = confirmer.add([0.99, 0.01, 1.21], 28, 0.02)

    assert result is not None
    assert result.observation_count == 3
    np.testing.assert_allclose(result.position, [1.0, 0.0, 1.2])
    assert result.max_deviation < 0.03
    assert result.covariance.shape == (3, 3)


def test_temporal_confirmation_rejects_scattered_positions():
    confirmer = TemporalTargetConfirmer(max_position_spread=0.25)

    confirmer.add([1.0, 0.0, 1.0], 20, 0.01)
    confirmer.add([1.1, 0.0, 1.0], 20, 0.01)
    result = confirmer.add([1.8, 0.0, 1.0], 20, 0.01)

    assert result is None
