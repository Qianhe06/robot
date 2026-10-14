"""Geometry and temporal confirmation for wall-mounted target localization."""

from collections import deque
from dataclasses import dataclass
import math

import numpy as np


_EPSILON = 1e-10


def _vector3(value, name):
    vector = np.asarray(value, dtype=np.float64)
    if vector.shape != (3,) or not np.isfinite(vector).all():
        raise ValueError(f"{name} must contain three finite values")
    return vector


def _unit_vector(value, name):
    vector = _vector3(value, name)
    norm = np.linalg.norm(vector)
    if norm <= _EPSILON:
        raise ValueError(f"{name} must be non-zero")
    return vector / norm


def pixel_to_camera_ray(pixel, camera_matrix):
    """Return a unit camera-frame ray through an undistorted image pixel."""
    pixel = np.asarray(pixel, dtype=np.float64)
    matrix = np.asarray(camera_matrix, dtype=np.float64)
    if pixel.shape != (2,) or not np.isfinite(pixel).all():
        raise ValueError("pixel must contain two finite values")
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise ValueError("camera_matrix must be a finite 3x3 matrix")

    homogeneous = np.array([pixel[0], pixel[1], 1.0], dtype=np.float64)
    try:
        ray = np.linalg.solve(matrix, homogeneous)
    except np.linalg.LinAlgError as error:
        raise ValueError("camera_matrix must be invertible") from error
    return _unit_vector(ray, "camera ray")


def intersect_ray_with_plane(
    ray_origin,
    ray_direction,
    plane_normal,
    plane_offset,
    min_distance=0.0,
):
    """Intersect a forward ray with ``normal dot point + offset = 0``."""
    origin = _vector3(ray_origin, "ray_origin")
    direction = _unit_vector(ray_direction, "ray_direction")
    normal = _unit_vector(plane_normal, "plane_normal")
    offset = float(plane_offset)
    minimum = float(min_distance)
    if (
        not math.isfinite(offset)
        or not math.isfinite(minimum)
        or minimum < 0.0
    ):
        raise ValueError("plane_offset and min_distance must be finite")

    denominator = float(np.dot(normal, direction))
    if abs(denominator) <= _EPSILON:
        return None
    distance = -(float(np.dot(normal, origin)) + offset) / denominator
    if distance < minimum:
        return None
    return origin + distance * direction


def project_pixel_to_plane(
    pixel,
    camera_matrix,
    camera_origin,
    camera_rotation,
    plane_normal,
    plane_offset,
    min_distance=0.0,
):
    """Project an undistorted pixel onto a plane in a shared world frame."""
    rotation = np.asarray(camera_rotation, dtype=np.float64)
    if rotation.shape != (3, 3) or not np.isfinite(rotation).all():
        raise ValueError("camera_rotation must be a finite 3x3 matrix")
    camera_ray = pixel_to_camera_ray(pixel, camera_matrix)
    world_ray = rotation @ camera_ray
    return intersect_ray_with_plane(
        camera_origin,
        world_ray,
        plane_normal,
        plane_offset,
        min_distance,
    )


def orient_wall_normal(plane_normal, plane_point, observer_position):
    """Orient a wall normal toward the observer-side free space."""
    normal = _unit_vector(plane_normal, "plane_normal")
    point = _vector3(plane_point, "plane_point")
    observer = _vector3(observer_position, "observer_position")
    if np.dot(normal, observer - point) < 0.0:
        normal = -normal
    return normal


@dataclass(frozen=True)
class PlaneFit:
    """A fitted plane and the input point indices that support it."""

    normal: np.ndarray
    offset: float
    inlier_indices: np.ndarray
    rms_error: float


def _refine_plane(points):
    center = np.mean(points, axis=0)
    _, _, right_vectors = np.linalg.svd(points - center, full_matrices=False)
    normal = _unit_vector(right_vectors[-1], "fitted plane normal")
    return normal, -float(np.dot(normal, center))


def fit_vertical_plane_ransac(
    points,
    distance_threshold=0.05,
    min_inliers=20,
    max_tilt_degrees=15.0,
    max_iterations=200,
    random_seed=None,
):
    """Fit a near-vertical wall plane with deterministic optional RANSAC."""
    values = np.asarray(points, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 3:
        raise ValueError("points must have shape (N, 3)")
    finite_mask = np.isfinite(values).all(axis=1)
    finite_indices = np.flatnonzero(finite_mask)
    values = values[finite_mask]

    threshold = float(distance_threshold)
    required = int(min_inliers)
    iterations = int(max_iterations)
    tilt = float(max_tilt_degrees)
    if threshold <= 0.0 or required < 3 or iterations < 1:
        raise ValueError("invalid RANSAC thresholds")
    if not 0.0 <= tilt < 90.0:
        raise ValueError("max_tilt_degrees must be in [0, 90)")
    if len(values) < required:
        raise ValueError("not enough finite points for the requested plane")

    max_vertical_component = math.sin(math.radians(tilt))
    generator = np.random.default_rng(random_seed)
    best_inliers = None
    best_error = math.inf

    for _ in range(iterations):
        sample = values[generator.choice(len(values), size=3, replace=False)]
        normal = np.cross(sample[1] - sample[0], sample[2] - sample[0])
        norm = np.linalg.norm(normal)
        if norm <= _EPSILON:
            continue
        normal /= norm
        if abs(normal[2]) > max_vertical_component:
            continue
        offset = -float(np.dot(normal, sample[0]))
        distances = np.abs(values @ normal + offset)
        inliers = np.flatnonzero(distances <= threshold)
        if len(inliers) < required:
            continue
        error = float(np.mean(np.square(distances[inliers])))
        if (
            best_inliers is None
            or len(inliers) > len(best_inliers)
            or (len(inliers) == len(best_inliers) and error < best_error)
        ):
            best_inliers = inliers
            best_error = error

    if best_inliers is None:
        raise ValueError("no vertical plane satisfies the inlier requirements")

    normal, offset = _refine_plane(values[best_inliers])
    if abs(normal[2]) > max_vertical_component:
        raise ValueError("refined plane exceeds the vertical tilt limit")
    distances = np.abs(values @ normal + offset)
    refined_inliers = np.flatnonzero(distances <= threshold)
    if len(refined_inliers) >= required:
        normal, offset = _refine_plane(values[refined_inliers])
        best_inliers = refined_inliers

    residuals = values[best_inliers] @ normal + offset
    rms_error = float(np.sqrt(np.mean(np.square(residuals))))
    return PlaneFit(
        normal=normal,
        offset=offset,
        inlier_indices=finite_indices[best_inliers],
        rms_error=rms_error,
    )


@dataclass(frozen=True)
class TargetConfirmation:
    """A temporally confirmed target position and its sample covariance."""

    position: np.ndarray
    covariance: np.ndarray
    observation_count: int
    max_deviation: float


class TemporalTargetConfirmer:
    """Confirm a stable target from a rolling window of frame observations."""

    def __init__(
        self,
        window_size=5,
        required_detections=3,
        min_support_points=20,
        max_plane_rms=0.05,
        max_position_spread=0.25,
    ):
        if window_size < 1 or not 1 <= required_detections <= window_size:
            raise ValueError("required_detections must fit inside window_size")
        if min_support_points < 1:
            raise ValueError("min_support_points must be positive")
        if max_plane_rms <= 0.0 or max_position_spread <= 0.0:
            raise ValueError("error and spread thresholds must be positive")

        self.required_detections = int(required_detections)
        self.min_support_points = int(min_support_points)
        self.max_plane_rms = float(max_plane_rms)
        self.max_position_spread = float(max_position_spread)
        self._observations = deque(maxlen=int(window_size))

    def reset(self):
        """Discard all observations in the current confirmation window."""
        self._observations.clear()

    def add(self, position, support_points, plane_rms):
        """Add a frame, using ``None`` when no target estimate is available."""
        valid = position is not None
        vector = None
        if valid:
            try:
                vector = _vector3(position, "position")
            except ValueError:
                valid = False
        valid = (
            valid
            and int(support_points) >= self.min_support_points
            and math.isfinite(float(plane_rms))
            and float(plane_rms) <= self.max_plane_rms
        )
        self._observations.append(vector if valid else None)
        return self.confirmation()

    def confirmation(self):
        """Return the current stable estimate, or ``None`` if not confirmed."""
        positions = [
            value for value in self._observations if value is not None
        ]
        if len(positions) < self.required_detections:
            return None

        positions = np.asarray(positions, dtype=np.float64)
        center = np.median(positions, axis=0)
        deviations = np.linalg.norm(positions - center, axis=1)
        max_deviation = float(np.max(deviations))
        if max_deviation > self.max_position_spread:
            return None

        covariance = np.zeros((3, 3), dtype=np.float64)
        if len(positions) > 1:
            covariance = np.cov(positions, rowvar=False, ddof=1)
        return TargetConfirmation(
            position=center,
            covariance=covariance,
            observation_count=len(positions),
            max_deviation=max_deviation,
        )
