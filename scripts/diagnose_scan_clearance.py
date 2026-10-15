#!/usr/bin/env python3
"""Report obstacle ranges around the stationary robot from one laser scan."""

import math
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan


class ScanClearanceDiagnostic(Node):
    """Collect one scan and summarize directional clearance."""

    def __init__(self):
        super().__init__("go2_scan_clearance_diagnostic")
        self.scan = None
        self.create_subscription(
            LaserScan,
            "/scan",
            self.scan_callback,
            qos_profile_sensor_data,
        )

    def scan_callback(self, message):
        self.scan = message

    def wait_for_scan(self, timeout=8.0):
        deadline = time.monotonic() + float(timeout)
        while rclpy.ok() and self.scan is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise RuntimeError("/scan data unavailable")
            rclpy.spin_once(self, timeout_sec=min(0.10, remaining))
        return self.scan


def valid_samples(message):
    """Return finite ``(angle_degrees, range, x, y)`` scan samples."""
    samples = []
    angle = float(message.angle_min)
    for distance in message.ranges:
        value = float(distance)
        if (
            math.isfinite(value)
            and float(message.range_min) <= value <= float(message.range_max)
        ):
            samples.append((
                math.degrees(angle),
                value,
                value * math.cos(angle),
                value * math.sin(angle),
            ))
        angle += float(message.angle_increment)
    return samples


def print_sector(samples, name, minimum_angle, maximum_angle):
    """Print the nearest return in one angular sector."""
    selected = [
        sample for sample in samples
        if minimum_angle <= sample[0] < maximum_angle
    ]
    if not selected:
        print(f"{name}: no finite return")
        return
    angle, distance, x, y = min(selected, key=lambda sample: sample[1])
    print(
        f"{name}: nearest={distance:.3f} m at {angle:+.1f} deg "
        f"(x={x:.3f}, y={y:.3f})"
    )


def main():
    """Read one scan and print clearance without commanding motion."""
    rclpy.init()
    node = ScanClearanceDiagnostic()
    try:
        message = node.wait_for_scan()
        samples = valid_samples(message)
        if not samples:
            raise RuntimeError("/scan contains no finite ranges")
        print(
            f"frame={message.header.frame_id}, "
            f"finite_samples={len(samples)}"
        )
        print_sector(samples, "front", -15.0, 15.0)
        print_sector(samples, "front-left", 15.0, 60.0)
        print_sector(samples, "left", 60.0, 120.0)
        print_sector(samples, "rear", 120.0, 181.0)
        print_sector(samples, "rear", -180.0, -120.0)
        print_sector(samples, "right", -120.0, -60.0)
        print_sector(samples, "front-right", -60.0, -15.0)

        forward = [sample for sample in samples if abs(sample[0]) < 30.0]
        for threshold in (0.35, 0.50, 0.80):
            count = sum(sample[1] < threshold for sample in forward)
            print(f"front +/-30 deg below {threshold:.2f} m: {count}")

        inside_footprint = [
            sample for sample in samples
            if abs(sample[2]) <= 0.38 and abs(sample[3]) <= 0.17
        ]
        print(
            "returns inside 0.76 x 0.34 m footprint: "
            f"{len(inside_footprint)}"
        )
        if inside_footprint:
            angle, distance, x, y = min(
                inside_footprint,
                key=lambda sample: sample[1],
            )
            print(
                f"nearest inside-footprint return={distance:.3f} m "
                f"at {angle:+.1f} deg (x={x:.3f}, y={y:.3f})"
            )
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
