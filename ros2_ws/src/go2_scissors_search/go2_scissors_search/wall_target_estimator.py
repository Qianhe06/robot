"""Estimate a wall-mounted target from one RGB detection and LiDAR frame."""

from dataclasses import dataclass
import math

import numpy as np

from .localization_core import fit_vertical_plane_ransac
from .localization_core import orient_wall_normal
from .localization_core import project_pixel_to_plane


@dataclass(frozen=True)
class WallTargetEstimate:
    """A target position and its supporting wall plane in the base frame."""

    position: np.ndarray
    wall_normal: np.ndarray
    wall_offset: float
    support_indices: np.ndarray
    plane_rms: float


def _validated_bbox(bbox):
    values = np.asarray(bbox, dtype=np.float64)
    if values.shape != (4,) or not np.isfinite(values).all():
        raise ValueError("bbox must contain four finite values")
    x1, y1, x2, y2 = values
    if x2 <= x1 or y2 <= y1:
        raise ValueError("bbox must have positive width and height")
    return values


def _project_base_points(points_base, rotation_camera_base, translation):
    points_camera = (rotation_camera_base @ points_base.T).T + translation
    return points_camera


def _wall_context_indices(
    projected,
    valid_indices,
    bbox,
    expansion_ratio,
):
    x1, y1, x2, y2 = bbox
    width = x2 - x1
    height = y2 - y1
    outer = np.array([
        x1 - expansion_ratio * width,
        y1 - expansion_ratio * height,
        x2 + expansion_ratio * width,
        y2 + expansion_ratio * height,
    ])

    pixels = projected[valid_indices]
    in_outer = (
        (pixels[:, 0] >= outer[0])
        & (pixels[:, 0] <= outer[2])
        & (pixels[:, 1] >= outer[1])
        & (pixels[:, 1] <= outer[3])
    )
    in_detection = (
        (pixels[:, 0] >= x1)
        & (pixels[:, 0] <= x2)
        & (pixels[:, 1] >= y1)
        & (pixels[:, 1] <= y2)
    )
    return valid_indices[in_outer & ~in_detection]


def estimate_wall_target(
    points_base,
    bbox,
    camera_matrix,
    rotation_camera_base,
    translation_camera_base,
    min_depth=0.2,
    max_depth=6.0,
    context_expansion_ratio=0.75,
    wall_distance_threshold=0.05,
    min_wall_support=20,
    max_wall_rms=0.05,
    max_wall_tilt_degrees=15.0,
    random_seed=None,
):
    """Estimate a target by intersecting its camera ray with a nearby wall."""
    points = np.asarray(points_base, dtype=np.float64)
    matrix = np.asarray(camera_matrix, dtype=np.float64)
    rotation = np.asarray(rotation_camera_base, dtype=np.float64)
    translation = np.asarray(translation_camera_base, dtype=np.float64)
    box = _validated_bbox(bbox)

    if points.ndim != 2 or points.shape[1] != 3:
        raise ValueError("points_base must have shape (N, 3)")
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise ValueError("camera_matrix must be a finite 3x3 matrix")
    if rotation.shape != (3, 3) or not np.isfinite(rotation).all():
        raise ValueError("rotation_camera_base must be a finite 3x3 matrix")
    if translation.shape != (3,) or not np.isfinite(translation).all():
        raise ValueError("translation_camera_base must contain three values")
    if min_depth < 0.0 or max_depth <= min_depth:
        raise ValueError("depth limits are invalid")
    if context_expansion_ratio <= 0.0:
        raise ValueError("context_expansion_ratio must be positive")

    points_camera = _project_base_points(points, rotation, translation)
    depths = points_camera[:, 2]
    valid = (
        np.isfinite(points_camera).all(axis=1)
        & (depths >= min_depth)
        & (depths <= max_depth)
    )
    valid_indices = np.flatnonzero(valid)
    if len(valid_indices) < min_wall_support:
        raise ValueError("not enough visible LiDAR points")

    homogeneous = (matrix @ points_camera[valid_indices].T).T
    projected = np.full((len(points), 2), np.nan, dtype=np.float64)
    projected[valid_indices] = (
        homogeneous[:, :2] / homogeneous[:, 2, np.newaxis]
    )
    context_indices = _wall_context_indices(
        projected,
        valid_indices,
        box,
        float(context_expansion_ratio),
    )
    if len(context_indices) < min_wall_support:
        raise ValueError("not enough wall points around the detection")

    plane = fit_vertical_plane_ransac(
        points[context_indices],
        distance_threshold=wall_distance_threshold,
        min_inliers=min_wall_support,
        max_tilt_degrees=max_wall_tilt_degrees,
        random_seed=random_seed,
    )
    if not math.isfinite(plane.rms_error) or plane.rms_error > max_wall_rms:
        raise ValueError("wall plane residual exceeds the allowed threshold")

    support_indices = context_indices[plane.inlier_indices]
    camera_origin_base = -(rotation.T @ translation)
    wall_point = np.mean(points[support_indices], axis=0)
    wall_normal = orient_wall_normal(
        plane.normal,
        wall_point,
        camera_origin_base,
    )
    wall_offset = plane.offset
    if np.dot(wall_normal, plane.normal) < 0.0:
        wall_offset = -wall_offset

    center_pixel = np.array([
        0.5 * (box[0] + box[2]),
        0.5 * (box[1] + box[3]),
    ])
    position = project_pixel_to_plane(
        center_pixel,
        matrix,
        camera_origin_base,
        rotation.T,
        wall_normal,
        wall_offset,
        min_distance=min_depth,
    )
    if position is None:
        raise ValueError("target camera ray does not intersect the wall ahead")
    if np.linalg.norm(position - camera_origin_base) > max_depth:
        raise ValueError("wall intersection exceeds the maximum depth")

    return WallTargetEstimate(
        position=position,
        wall_normal=wall_normal,
        wall_offset=wall_offset,
        support_indices=support_indices,
        plane_rms=plane.rms_error,
    )
