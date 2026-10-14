"""Tests for single-frame wall target estimation."""

import numpy as np
import pytest

from go2_scissors_search.wall_target_estimator import estimate_wall_target


CAMERA_MATRIX = np.array([
    [500.0, 0.0, 320.0],
    [0.0, 500.0, 240.0],
    [0.0, 0.0, 1.0],
])

# Base frame: x forward, y left, z up. Camera frame: x right, y down,
# z forward. This matches the axis convention used by the application.
ROTATION_CAMERA_BASE = np.array([
    [0.0, -1.0, 0.0],
    [0.0, 0.0, -1.0],
    [1.0, 0.0, 0.0],
])


def _wall_cloud(seed=12):
    generator = np.random.default_rng(seed)
    wall = np.column_stack((
        2.0 + generator.normal(0.0, 0.004, 600),
        generator.uniform(-0.9, 0.9, 600),
        generator.uniform(-0.7, 0.7, 600),
    ))
    outliers = generator.uniform(
        low=[0.5, -1.0, -0.8],
        high=[4.0, 1.0, 0.8],
        size=(60, 3),
    )
    return np.vstack((wall, outliers))


def test_estimator_intersects_detection_center_with_wall():
    estimate = estimate_wall_target(
        _wall_cloud(),
        bbox=[285.0, 205.0, 355.0, 275.0],
        camera_matrix=CAMERA_MATRIX,
        rotation_camera_base=ROTATION_CAMERA_BASE,
        translation_camera_base=np.zeros(3),
        min_wall_support=40,
        random_seed=3,
    )

    np.testing.assert_allclose(estimate.position, [2.0, 0.0, 0.0], atol=0.02)
    assert estimate.wall_normal[0] < -0.99
    assert len(estimate.support_indices) > 90
    assert estimate.plane_rms < 0.01


def test_estimator_uses_detection_center_for_offset_target():
    estimate = estimate_wall_target(
        _wall_cloud(),
        bbox=[385.0, 255.0, 455.0, 325.0],
        camera_matrix=CAMERA_MATRIX,
        rotation_camera_base=ROTATION_CAMERA_BASE,
        translation_camera_base=np.zeros(3),
        min_wall_support=30,
        random_seed=8,
    )

    np.testing.assert_allclose(
        estimate.position,
        [2.0, -0.4, -0.2],
        atol=0.03,
    )


def test_estimator_rejects_detection_without_wall_context():
    with pytest.raises(ValueError, match="(visible LiDAR|wall points)"):
        estimate_wall_target(
            _wall_cloud()[:10],
            bbox=[285.0, 205.0, 355.0, 275.0],
            camera_matrix=CAMERA_MATRIX,
            rotation_camera_base=ROTATION_CAMERA_BASE,
            translation_camera_base=np.zeros(3),
            min_wall_support=20,
            random_seed=3,
        )
