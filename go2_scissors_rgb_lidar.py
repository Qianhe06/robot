#!/usr/bin/env python3
"""Detect scissors in RGB, associate LiDAR points, and publish 3D positions."""

import math
import struct
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np
import open3d as o3d
import rclpy
import torch
from cv_bridge import CvBridge
from geometry_msgs.msg import PointStamped
from nav_msgs.msg import Odometry
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image, PointCloud2, PointField
from sensor_msgs_py import point_cloud2
from std_msgs.msg import Header
from ultralytics import YOLO
from visualization_msgs.msg import Marker


# ROS topics
IMAGE_TOPIC = "/camera/image_raw"
INFO_TOPIC = "/camera/camera_info"
CLOUD_TOPIC = "/utlidar/cloud_base"
ODOM_TOPIC = "/utlidar/robot_odom"

# Detector
MODEL_NAME = "yolo26x.pt"
PROJECT_DIR = Path(__file__).resolve().parent
MODEL_CANDIDATES = (
    PROJECT_DIR / MODEL_NAME,
    PROJECT_DIR.parent / "unitree_sdk2-2.0.2" / MODEL_NAME,
    Path(MODEL_NAME),
)
MODEL_PATH = str(next((path for path in MODEL_CANDIDATES if path.exists()), MODEL_NAME))
TARGET_CLASS = "scissors"
CONF = 0.50
IOU = 0.50
IMGSZ = 640
DEVICE = 1

# Latest 11-pose joint optimization result from the calibration record.
# Convention: p_camera = R_CAM_BODY @ p_base_link + T_CAM_BODY
R_CAM_BODY = np.array([
    [0.052406962, -0.997912281, 0.037743734],
    [0.063994729, -0.034362001, -0.997358475],
    [0.996573221, 0.054683928, 0.062060318],
], dtype=np.float64)

T_CAM_BODY = np.array([
    -0.004925410,
    -0.035296691,
    -0.355869834,
], dtype=np.float64)

# Association parameters
MIN_DEPTH = 0.20
MAX_DEPTH = 6.00
BBOX_INSET_RATIO = 0.03
DEPTH_CLUSTER_GAP_M = 0.10
DEPTH_CLUSTER_MARGIN_M = 0.04
MIN_CLUSTER_POINTS = 1
MAX_CLUSTER_DIAMETER_M = 0.35
DETECTION_HOLD_SEC = 0.50

# 0.0 crops/zooms enough to remove invalid black borders while preserving the
# original image resolution. LiDAR projection uses the resulting new K matrix.
UNDISTORT_ALPHA = 0.0

# Maintain a rolling window of the latest ten LiDAR messages.
ACCUMULATION_FRAMES = 10
SNAPSHOT_PLY_PATH = PROJECT_DIR / "scissors_10frame_snapshot.ply"
RGB_SNAPSHOT_PATH = PROJECT_DIR / "scissors_rgb_detection.png"

# Visualization. RGB projection remains available as a ROS debug topic, while
# the main local window is the 3D Open3D point cloud.
SHOW_OPEN3D = True
SHOW_RGB_PREVIEW = True
ENVIRONMENT_COLOR = np.array([0.55, 0.55, 0.55], dtype=np.float64)
TARGET_COLOR = np.array([1.00, 0.02, 0.02], dtype=np.float64)


def packed_rgb_float(red, green, blue):
    value = (int(red) << 16) | (int(green) << 8) | int(blue)
    return struct.unpack("f", struct.pack("I", value))[0]


GRAY_RGB = packed_rgb_float(125, 125, 125)
RED_RGB = packed_rgb_float(255, 30, 30)


def quaternion_rotation_matrix(x, y, z, w):
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm < 1e-12:
        return np.eye(3, dtype=np.float64)
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ], dtype=np.float64)


class ScissorsRGBLidar(Node):
    def __init__(self):
        super().__init__("go2_scissors_rgb_lidar")
        self.bridge = CvBridge()
        self.model = YOLO(MODEL_PATH)
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is not available")
        self.target_class_ids = [
            int(class_id)
            for class_id, name in self.model.names.items()
            if str(name).lower() == TARGET_CLASS
        ]
        if not self.target_class_ids:
            raise RuntimeError(f"Model has no class named '{TARGET_CLASS}'")

        self.camera_matrix = None
        self.distortion = None
        self.rectified_camera_matrix = None
        self.undistort_map1 = None
        self.undistort_map2 = None
        self.undistort_image_size = None
        self.cloud_frames = deque(maxlen=ACCUMULATION_FRAMES)
        self.collection_complete = False
        self.latest_cloud = None
        self.latest_cloud_header = None
        self.latest_odom = None
        self.last_wait_log_time = 0.0
        self.last_position_log_time = 0.0
        self.cached_detections = []
        self.cached_detection_time = 0.0
        self.stop_requested = False
        self.open3d_enabled = False
        self.open3d_view_initialized = False

        self.create_subscription(
            CameraInfo, INFO_TOPIC, self.info_callback, qos_profile_sensor_data
        )
        self.create_subscription(
            PointCloud2, CLOUD_TOPIC, self.cloud_callback, qos_profile_sensor_data
        )
        self.create_subscription(
            Odometry, ODOM_TOPIC, self.odom_callback, qos_profile_sensor_data
        )
        self.create_subscription(
            Image, IMAGE_TOPIC, self.image_callback, qos_profile_sensor_data
        )

        self.cloud_pub = self.create_publisher(
            PointCloud2, "/scissors/colored_cloud", 10
        )
        self.base_position_pub = self.create_publisher(
            PointStamped, "/scissors/position_base", 10
        )
        self.odom_position_pub = self.create_publisher(
            PointStamped, "/scissors/position_odom", 10
        )
        self.marker_pub = self.create_publisher(Marker, "/scissors/marker", 10)
        self.overlay_pub = self.create_publisher(
            Image, "/scissors/detection_image", 10
        )
        self.rectified_pub = self.create_publisher(
            Image, "/scissors/image_rectified", 10
        )

        if SHOW_OPEN3D:
            self.initialize_open3d()
            self.create_timer(1.0 / 30.0, self.open3d_event_loop)

        self.get_logger().info(
            f"Scissors RGB-LiDAR node ready: model={MODEL_PATH}, conf={CONF:.2f}, "
            f"GPU={DEVICE} ({torch.cuda.get_device_name(DEVICE)})"
        )
        self.get_logger().info(
            f"Waiting for {ACCUMULATION_FRAMES} LiDAR frames; then using a rolling window"
        )

    def initialize_open3d(self):
        self.visualizer = o3d.visualization.Visualizer()
        self.open3d_enabled = self.visualizer.create_window(
            window_name="Go2 Scissors 3D Localization",
            width=1280,
            height=720,
        )
        if not self.open3d_enabled:
            self.get_logger().warning("Open3D window creation failed")
            return

        self.o3d_cloud = o3d.geometry.PointCloud()
        self.o3d_cloud.points = o3d.utility.Vector3dVector(
            np.zeros((1, 3), dtype=np.float64)
        )
        self.o3d_cloud.colors = o3d.utility.Vector3dVector(
            ENVIRONMENT_COLOR.reshape(1, 3)
        )
        self.o3d_box = o3d.geometry.LineSet()
        self.coordinate_frame = o3d.geometry.TriangleMesh.create_coordinate_frame(
            size=0.30, origin=[0.0, 0.0, 0.0]
        )
        self.visualizer.add_geometry(self.o3d_cloud)
        self.visualizer.add_geometry(self.o3d_box)
        self.visualizer.add_geometry(self.coordinate_frame)

        render = self.visualizer.get_render_option()
        render.background_color = np.array([0.03, 0.03, 0.03])
        render.point_size = 4.0
        render.line_width = 5.0
        self.get_logger().info(
            "Open3D enabled: gray=environment, red=scissors, red box=3D extent"
        )

    @staticmethod
    def bounding_box_lines(points):
        minimum = points.min(axis=0)
        maximum = points.max(axis=0)
        center = 0.5 * (minimum + maximum)
        half_extent = np.maximum(0.5 * (maximum - minimum), 0.04)
        low = center - half_extent
        high = center + half_extent
        corners = np.array([
            [low[0], low[1], low[2]],
            [high[0], low[1], low[2]],
            [high[0], high[1], low[2]],
            [low[0], high[1], low[2]],
            [low[0], low[1], high[2]],
            [high[0], low[1], high[2]],
            [high[0], high[1], high[2]],
            [low[0], high[1], high[2]],
        ], dtype=np.float64)
        lines = np.array([
            [0, 1], [1, 2], [2, 3], [3, 0],
            [4, 5], [5, 6], [6, 7], [7, 4],
            [0, 4], [1, 5], [2, 6], [3, 7],
        ], dtype=np.int32)
        return corners, lines

    def update_open3d(self, points_body, selected_indices):
        if not self.open3d_enabled:
            return

        colors = np.tile(ENVIRONMENT_COLOR, (len(points_body), 1))
        colors[selected_indices] = TARGET_COLOR
        self.o3d_cloud.points = o3d.utility.Vector3dVector(points_body)
        self.o3d_cloud.colors = o3d.utility.Vector3dVector(colors)

        if len(selected_indices):
            corners, lines = self.bounding_box_lines(points_body[selected_indices])
            self.o3d_box.points = o3d.utility.Vector3dVector(corners)
            self.o3d_box.lines = o3d.utility.Vector2iVector(lines)
            self.o3d_box.colors = o3d.utility.Vector3dVector(
                np.tile(TARGET_COLOR, (len(lines), 1))
            )
        else:
            self.o3d_box.points = o3d.utility.Vector3dVector(
                np.empty((0, 3), dtype=np.float64)
            )
            self.o3d_box.lines = o3d.utility.Vector2iVector(
                np.empty((0, 2), dtype=np.int32)
            )
            self.o3d_box.colors = o3d.utility.Vector3dVector(
                np.empty((0, 3), dtype=np.float64)
            )

        self.visualizer.update_geometry(self.o3d_cloud)
        self.visualizer.update_geometry(self.o3d_box)
        if not self.open3d_view_initialized:
            self.visualizer.reset_view_point(True)
            self.open3d_view_initialized = True
        if not self.visualizer.poll_events():
            self.open3d_enabled = False
            self.request_stop("Open3D window closed")
            return
        self.visualizer.update_renderer()

    def open3d_event_loop(self):
        """Keep the Open3D window interactive between sensor callbacks."""
        if not self.open3d_enabled:
            return
        if not self.visualizer.poll_events():
            self.open3d_enabled = False
            self.request_stop("Open3D window closed")
            return
        self.visualizer.update_renderer()

    def request_stop(self, reason):
        if not self.stop_requested:
            self.get_logger().info(reason)
            self.stop_requested = True

    @staticmethod
    def save_snapshot(points_body, selected_indices):
        colors = np.tile(ENVIRONMENT_COLOR, (len(points_body), 1))
        colors[selected_indices] = TARGET_COLOR
        cloud = o3d.geometry.PointCloud()
        cloud.points = o3d.utility.Vector3dVector(points_body)
        cloud.colors = o3d.utility.Vector3dVector(colors)
        return o3d.io.write_point_cloud(str(SNAPSHOT_PLY_PATH), cloud)

    def info_callback(self, msg):
        camera_matrix = np.asarray(msg.k, dtype=np.float64).reshape(3, 3)
        distortion = np.asarray(msg.d, dtype=np.float64).reshape(-1)
        calibration_changed = (
            self.camera_matrix is None
            or self.distortion is None
            or not np.allclose(self.camera_matrix, camera_matrix)
            or self.distortion.shape != distortion.shape
            or not np.allclose(self.distortion, distortion)
        )
        self.camera_matrix = camera_matrix
        self.distortion = distortion
        if calibration_changed:
            self.rectified_camera_matrix = None
            self.undistort_map1 = None
            self.undistort_map2 = None
            self.undistort_image_size = None

    def ensure_undistortion_maps(self, width, height):
        image_size = (int(width), int(height))
        if (
            self.undistort_map1 is not None
            and self.undistort_map2 is not None
            and self.undistort_image_size == image_size
        ):
            return

        self.rectified_camera_matrix, valid_roi = cv2.getOptimalNewCameraMatrix(
            self.camera_matrix,
            self.distortion,
            image_size,
            UNDISTORT_ALPHA,
            image_size,
            centerPrincipalPoint=False,
        )
        self.undistort_map1, self.undistort_map2 = cv2.initUndistortRectifyMap(
            self.camera_matrix,
            self.distortion,
            None,
            self.rectified_camera_matrix,
            image_size,
            cv2.CV_16SC2,
        )
        self.undistort_image_size = image_size
        self.get_logger().info(
            f"RGB undistortion map ready: size={image_size}, "
            f"alpha={UNDISTORT_ALPHA:.1f}, valid_roi={valid_roi}"
        )

    def cloud_callback(self, msg):
        rows = point_cloud2.read_points(
            msg, field_names=("x", "y", "z"), skip_nans=True
        )
        points = np.asarray(
            [[float(p[0]), float(p[1]), float(p[2])] for p in rows],
            dtype=np.float64,
        )
        if points.size == 0:
            return
        self.cloud_frames.append(points.reshape(-1, 3))

        if len(self.cloud_frames) < ACCUMULATION_FRAMES:
            self.get_logger().info(
                f"LiDAR accumulation: {len(self.cloud_frames)}/{ACCUMULATION_FRAMES}"
            )
            return

        self.latest_cloud = np.concatenate(list(self.cloud_frames), axis=0)
        self.latest_cloud_header = msg.header
        if not self.collection_complete:
            self.collection_complete = True
            self.get_logger().info(
                f"Rolling LiDAR window ready: {len(self.latest_cloud)} points"
            )

    def odom_callback(self, msg):
        self.latest_odom = msg

    @staticmethod
    def choose_depth_cluster(candidate_indices, points_camera, projected, bbox):
        """Choose a compact depth layer inside a 2D detection box."""
        if len(candidate_indices) < MIN_CLUSTER_POINTS:
            return np.empty(0, dtype=np.int64)

        depths = points_camera[candidate_indices, 2]
        order = np.argsort(depths)
        sorted_indices = candidate_indices[order]
        sorted_depths = depths[order]
        split_locations = np.flatnonzero(
            np.diff(sorted_depths) > DEPTH_CLUSTER_GAP_M
        ) + 1
        clusters = np.split(sorted_indices, split_locations)
        clusters = [c for c in clusters if len(c) >= MIN_CLUSTER_POINTS]
        if not clusters:
            return np.empty(0, dtype=np.int64)

        x1, y1, x2, y2 = bbox
        box_center = np.array([(x1 + x2) * 0.5, (y1 + y2) * 0.5])
        box_diagonal = max(math.hypot(x2 - x1, y2 - y1), 1.0)

        def score(cluster):
            pixel_center = np.median(projected[cluster], axis=0)
            center_error = np.linalg.norm(pixel_center - box_center) / box_diagonal
            return center_error - 0.02 * math.log1p(len(cluster))

        chosen = min(clusters, key=score)
        chosen_depth = np.median(points_camera[chosen, 2])
        expanded = candidate_indices[
            np.abs(points_camera[candidate_indices, 2] - chosen_depth)
            <= DEPTH_CLUSTER_MARGIN_M
        ]

        center = np.median(points_camera[expanded], axis=0)
        spatial_distance = np.linalg.norm(points_camera[expanded] - center, axis=1)
        compact = expanded[spatial_distance <= MAX_CLUSTER_DIAMETER_M]
        return compact if len(compact) >= MIN_CLUSTER_POINTS else chosen

    def associate_box(self, bbox, points_camera, projected, valid_indices):
        x1, y1, x2, y2 = [float(value) for value in bbox]
        inset_x = (x2 - x1) * BBOX_INSET_RATIO
        inset_y = (y2 - y1) * BBOX_INSET_RATIO
        uv = projected[valid_indices]
        inside = (
            (uv[:, 0] >= x1 + inset_x)
            & (uv[:, 0] <= x2 - inset_x)
            & (uv[:, 1] >= y1 + inset_y)
            & (uv[:, 1] <= y2 - inset_y)
        )
        candidates = valid_indices[inside]
        selected = self.choose_depth_cluster(
            candidates, points_camera, projected, (x1, y1, x2, y2)
        )
        return candidates, selected

    def publish_colored_cloud(self, points_body, selected_indices, header):
        colors = np.full(len(points_body), GRAY_RGB, dtype=np.float32)
        colors[selected_indices] = RED_RGB
        fields = [
            PointField(name="x", offset=0, datatype=PointField.FLOAT32, count=1),
            PointField(name="y", offset=4, datatype=PointField.FLOAT32, count=1),
            PointField(name="z", offset=8, datatype=PointField.FLOAT32, count=1),
            PointField(name="rgb", offset=12, datatype=PointField.FLOAT32, count=1),
        ]
        cloud_header = Header()
        cloud_header.stamp = header.stamp
        cloud_header.frame_id = header.frame_id or "base_link"
        rows = np.column_stack((points_body.astype(np.float32), colors))
        self.cloud_pub.publish(point_cloud2.create_cloud(cloud_header, fields, rows))

    def publish_positions(self, position_base, stamp, confidence, point_count):
        base_msg = PointStamped()
        base_msg.header.stamp = stamp
        base_msg.header.frame_id = "base_link"
        base_msg.point.x, base_msg.point.y, base_msg.point.z = map(float, position_base)
        self.base_position_pub.publish(base_msg)

        marker_frame = "base_link"
        marker_position = position_base
        if self.latest_odom is not None:
            pose = self.latest_odom.pose.pose
            q = pose.orientation
            rotation = quaternion_rotation_matrix(q.x, q.y, q.z, q.w)
            translation = np.array(
                [pose.position.x, pose.position.y, pose.position.z], dtype=np.float64
            )
            marker_position = rotation @ position_base + translation
            marker_frame = self.latest_odom.header.frame_id or "odom"
            odom_msg = PointStamped()
            odom_msg.header.stamp = stamp
            odom_msg.header.frame_id = marker_frame
            odom_msg.point.x, odom_msg.point.y, odom_msg.point.z = map(
                float, marker_position
            )
            self.odom_position_pub.publish(odom_msg)

        marker = Marker()
        marker.header.stamp = stamp
        marker.header.frame_id = marker_frame
        marker.ns = "scissors"
        marker.id = 0
        marker.type = Marker.SPHERE
        marker.action = Marker.ADD
        marker.pose.orientation.w = 1.0
        marker.pose.position.x, marker.pose.position.y, marker.pose.position.z = map(
            float, marker_position
        )
        marker.scale.x = marker.scale.y = marker.scale.z = 0.10
        marker.color.r = 1.0
        marker.color.g = 0.05
        marker.color.b = 0.05
        marker.color.a = 1.0
        self.marker_pub.publish(marker)

        now = time.monotonic()
        if now - self.last_position_log_time >= 1.0:
            self.get_logger().info(
                f"scissors conf={confidence:.2f}, lidar_points={point_count}, "
                f"base_link=({position_base[0]:.3f}, {position_base[1]:.3f}, "
                f"{position_base[2]:.3f}) m"
            )
            self.last_position_log_time = now

    def image_callback(self, msg):
        if (
            self.camera_matrix is None
            or not self.collection_complete
            or self.latest_cloud is None
        ):
            return

        try:
            raw_image = self.bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
        except Exception as error:
            self.get_logger().error(f"cv_bridge failed: {error}")
            return

        raw_height, raw_width = raw_image.shape[:2]
        self.ensure_undistortion_maps(raw_width, raw_height)
        image = cv2.remap(
            raw_image,
            self.undistort_map1,
            self.undistort_map2,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
        )
        rectified_msg = self.bridge.cv2_to_imgmsg(image, encoding="bgr8")
        rectified_msg.header = msg.header
        self.rectified_pub.publish(rectified_msg)

        result = self.model.predict(
            source=image,
            imgsz=IMGSZ,
            conf=CONF,
            iou=IOU,
            device=DEVICE,
            classes=self.target_class_ids,
            verbose=False,
        )[0]
        torch.cuda.synchronize(DEVICE)
        display = result.plot()

        points_body = self.latest_cloud.copy()
        cloud_header = self.latest_cloud_header
        points_camera = (R_CAM_BODY @ points_body.T).T + T_CAM_BODY
        depths = points_camera[:, 2]
        valid = (
            np.isfinite(points_camera).all(axis=1)
            & (depths >= MIN_DEPTH)
            & (depths <= MAX_DEPTH)
        )
        valid_indices = np.flatnonzero(valid)
        projected = np.full((len(points_body), 2), np.nan, dtype=np.float64)
        if len(valid_indices):
            uv, _ = cv2.projectPoints(
                points_camera[valid_indices],
                np.zeros((3, 1), dtype=np.float64),
                np.zeros((3, 1), dtype=np.float64),
                self.rectified_camera_matrix,
                None,
            )
            projected[valid_indices] = uv.reshape(-1, 2)

        detections = []
        associations = []
        current_box_detections = []
        target_box_count = 0
        if result.boxes is not None:
            for box in result.boxes:
                class_id = int(box.cls[0].item())
                class_name = str(self.model.names[class_id]).lower()
                if class_name != TARGET_CLASS:
                    continue
                target_box_count += 1
                confidence = float(box.conf[0].item())
                bbox = box.xyxy[0].detach().cpu().numpy()
                current_box_detections.append((confidence, bbox.copy()))
                candidates, selected = self.associate_box(
                    bbox, points_camera, projected, valid_indices
                )
                associations.append((confidence, bbox, candidates, selected))
                if len(selected) >= MIN_CLUSTER_POINTS:
                    detections.append((confidence, bbox, candidates, selected))

        now = time.monotonic()
        if current_box_detections:
            self.cached_detections = current_box_detections
            self.cached_detection_time = now
        elif (
            self.cached_detections
            and now - self.cached_detection_time <= DETECTION_HOLD_SEC
        ):
            for confidence, bbox in self.cached_detections:
                candidates, selected = self.associate_box(
                    bbox, points_camera, projected, valid_indices
                )
                associations.append((confidence, bbox, candidates, selected))
                target_box_count += 1
                if len(selected) >= MIN_CLUSTER_POINTS:
                    detections.append((confidence, bbox, candidates, selected))
                x1, y1, x2, y2 = np.rint(bbox).astype(int)
                cv2.rectangle(display, (x1, y1), (x2, y2), (0, 200, 255), 2)
                cv2.putText(
                    display,
                    f"scissors {confidence:.2f} (held)",
                    (max(5, x1), max(25, y1 - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (0, 200, 255),
                    2,
                    cv2.LINE_AA,
                )

        if target_box_count == 0:
            if now - self.last_wait_log_time > 2.0:
                self.get_logger().info(
                    "No scissors detected in the current RGB frame"
                )
                self.last_wait_log_time = now

        height, width = display.shape[:2]
        visible_uv = projected[valid_indices]
        visible_mask = (
            np.isfinite(visible_uv).all(axis=1)
            & (visible_uv[:, 0] >= 0)
            & (visible_uv[:, 0] < width)
            & (visible_uv[:, 1] >= 0)
            & (visible_uv[:, 1] < height)
        )
        for u, v in np.rint(visible_uv[visible_mask]).astype(int):
            cv2.circle(display, (u, v), 1, (255, 180, 0), -1)

        for _, bbox, candidates, selected in associations:
            for index in candidates:
                u, v = np.rint(projected[index]).astype(int)
                cv2.circle(display, (u, v), 3, (0, 255, 255), -1)
            for index in selected:
                u, v = np.rint(projected[index]).astype(int)
                cv2.circle(display, (u, v), 6, (255, 0, 255), -1)
            label_x = max(10, int(bbox[0]))
            label_y = min(height - 10, max(25, int(bbox[3]) + 28))
            cv2.putText(
                display,
                f"LiDAR in box: {len(candidates)}  selected: {len(selected)}",
                (label_x, label_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (0, 255, 255),
                2,
                cv2.LINE_AA,
            )

        selected_all = np.empty(0, dtype=np.int64)
        if detections:
            selected_all = np.unique(
                np.concatenate([item[3] for item in detections])
            )
            best_confidence, _, _, best_selected = max(
                detections, key=lambda item: item[0]
            )
            position_base = np.median(points_body[best_selected], axis=0)
            self.publish_positions(
                position_base,
                cloud_header.stamp,
                best_confidence,
                len(best_selected),
            )
            cv2.putText(
                display,
                f"3D base: {position_base[0]:.2f}, {position_base[1]:.2f}, "
                f"{position_base[2]:.2f} m | points: {len(best_selected)}",
                (25, 45),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.75,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )
        else:
            status = (
                "Scissors detected, but no LiDAR point projects inside its box"
                if target_box_count
                else "No scissors detected"
            )
            cv2.putText(
                display,
                status,
                (25, 45),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.75,
                (0, 165, 255),
                2,
                cv2.LINE_AA,
            )

        cv2.putText(
            display,
            "Blue: projected LiDAR  Yellow: in box  Magenta: selected",
            (25, height - 55),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            display,
            "Lens distortion corrected",
            (25, 80),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            display,
            "SPACE: show latest 10 frames in Open3D   Q: quit   S: save",
            (25, height - 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

        overlay_msg = self.bridge.cv2_to_imgmsg(display, encoding="bgr8")
        overlay_msg.header = msg.header
        self.overlay_pub.publish(overlay_msg)

        if SHOW_RGB_PREVIEW:
            preview = cv2.resize(display, (1280, 720))
            window_name = "Go2 Scissors RGB Detection"
            cv2.imshow(window_name, preview)
            key = cv2.waitKey(1) & 0xFF
            if key == ord(" "):
                self.publish_colored_cloud(points_body, selected_all, cloud_header)
                self.update_open3d(points_body, selected_all)
                self.get_logger().info(
                    f"Open3D snapshot updated: latest {ACCUMULATION_FRAMES} "
                    f"frames, {len(points_body)} total points, "
                    f"{len(selected_all)} scissors points"
                )
            elif key == ord("s"):
                cv2.imwrite(str(RGB_SNAPSHOT_PATH), display)
                saved = self.save_snapshot(points_body, selected_all)
                self.get_logger().info(
                    f"Saved RGB={RGB_SNAPSHOT_PATH}, cloud={SNAPSHOT_PLY_PATH}, "
                    f"success={saved}"
                )
            elif key == ord("q"):
                self.request_stop("Q pressed in RGB window")
                return

            try:
                if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
                    self.request_stop("RGB window closed")
            except cv2.error:
                self.request_stop("RGB window closed")


def main():
    rclpy.init()
    node = ScissorsRGBLidar()
    try:
        while rclpy.ok() and not node.stop_requested:
            rclpy.spin_once(node, timeout_sec=0.02)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        cv2.destroyAllWindows()
        if hasattr(node, "visualizer"):
            node.visualizer.destroy_window()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
