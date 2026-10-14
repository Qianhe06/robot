#!/usr/bin/env python3

import time
import cv2
import torch
import rclpy

from rclpy.node import Node
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
from ultralytics import YOLO


IMAGE_TOPIC = "/camera/image_raw"

MODEL_PATH = "yolo26l.pt"

CONF = 0.6
IOU = 0.50
IMGSZ = 640

# 你的第二张4090
DEVICE = 1


class YOLO26LiveDemo(Node):

    def __init__(self):

        super().__init__(
            "go2_yolo26_live_demo"
        )

        self.bridge = CvBridge()

        print()
        print("==========================================")
        print("Go2 YOLO26 Live Demo")
        print("==========================================")
        print("Topic :", IMAGE_TOPIC)
        print("Model :", MODEL_PATH)
        print("GPU   :", DEVICE)
        print("==========================================")
        print()

        print("[INFO] Loading YOLO26...")

        self.model = YOLO(
            MODEL_PATH
        )

        print("[INFO] Model loaded")

        if not torch.cuda.is_available():
            raise RuntimeError(
                "CUDA is not available"
            )

        print(
            "[INFO] GPU:",
            torch.cuda.get_device_name(
                DEVICE
            )
        )

        self.sub = self.create_subscription(
            Image,
            IMAGE_TOPIC,
            self.image_callback,
            10
        )

        self.last_time = time.time()

        self.fps = 0.0
        self.frame_count = 0

        self.get_logger().info(
            "YOLO26 live demo started"
        )


    def image_callback(self, msg):

        # ==========================================
        # ROS image -> OpenCV
        # ==========================================

        try:

            frame = self.bridge.imgmsg_to_cv2(
                msg,
                desired_encoding="bgr8"
            )

        except Exception as e:

            print(
                "[ERROR] cv_bridge:",
                e
            )

            return


        # ==========================================
        # YOLO26 inference
        # ==========================================

        t0 = time.time()

        results = self.model.predict(
            source=frame,
            imgsz=IMGSZ,
            conf=CONF,
            iou=IOU,
            device=DEVICE,
            verbose=False
        )

        torch.cuda.synchronize(
            DEVICE
        )

        inference_ms = (
            time.time()
            - t0
        ) * 1000.0


        # ==========================================
        # Get annotated frame
        # ==========================================

        result = results[0]

        annotated = result.plot()


        # ==========================================
        # FPS
        # ==========================================

        now = time.time()

        dt = now - self.last_time

        self.last_time = now

        if dt > 0:

            current_fps = 1.0 / dt

            if self.fps == 0:
                self.fps = current_fps
            else:
                self.fps = (
                    0.9 * self.fps
                    +
                    0.1 * current_fps
                )


        # ==========================================
        # Detection count
        # ==========================================

        object_count = 0

        if result.boxes is not None:
            object_count = len(
                result.boxes
            )


        # ==========================================
        # Draw status
        # ==========================================

        cv2.putText(
            annotated,
            f"YOLO26n  GPU:{DEVICE}",
            (30, 45),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 255, 0),
            2,
            cv2.LINE_AA
        )

        cv2.putText(
            annotated,
            f"FPS: {self.fps:.1f}",
            (30, 85),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 255, 0),
            2,
            cv2.LINE_AA
        )

        cv2.putText(
            annotated,
            f"Inference: {inference_ms:.1f} ms",
            (30, 125),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 0),
            2,
            cv2.LINE_AA
        )

        cv2.putText(
            annotated,
            f"Objects: {object_count}",
            (30, 165),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 0),
            2,
            cv2.LINE_AA
        )


        # ==========================================
        # Resize preview
        # ==========================================

        preview = cv2.resize(
            annotated,
            (1280, 720)
        )

        cv2.imshow(
            "Go2 YOLO26 Live Detection",
            preview
        )

        key = (
            cv2.waitKey(1)
            & 0xFF
        )

        if key == ord("q"):
            raise KeyboardInterrupt


        # ==========================================
        # Terminal info
        # ==========================================

        self.frame_count += 1

        if self.frame_count % 30 == 0:

            print()
            print(
                f"FPS={self.fps:.2f} "
                f"Inference={inference_ms:.2f}ms "
                f"Objects={object_count}"
            )

            if result.boxes is not None:

                for box in result.boxes:

                    cls_id = int(
                        box.cls[0].item()
                    )

                    conf = float(
                        box.conf[0].item()
                    )

                    name = self.model.names[
                        cls_id
                    ]

                    print(
                        f"  {name:<15} "
                        f"{conf:.2f}"
                    )


def main():

    rclpy.init()

    node = YOLO26LiveDemo()

    try:

        rclpy.spin(
            node
        )

    except KeyboardInterrupt:

        print()
        print(
            "Stopped."
        )

    finally:

        try:
            node.destroy_node()
        except Exception:
            pass

        if rclpy.ok():
            try:
                rclpy.shutdown()
            except Exception:
                pass

        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
