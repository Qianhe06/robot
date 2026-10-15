#!/usr/bin/env python3
"""Check Nav2 action servers directly, without the ROS CLI daemon."""

import rclpy
from nav2_msgs.action import ComputePathToPose, NavigateToPose
from rclpy.action import ActionClient


def main():
    rclpy.init()
    node = rclpy.create_node("go2_nav2_action_preflight")
    try:
        actions = (
            ("/navigate_to_pose", NavigateToPose),
            ("/compute_path_to_pose", ComputePathToPose),
        )
        missing = []
        for name, action_type in actions:
            client = ActionClient(node, action_type, name)
            available = client.wait_for_server(timeout_sec=5.0)
            print(f"{name}: {'available' if available else 'unavailable'}")
            if not available:
                missing.append(name)
            client.destroy()
        return 1 if missing else 0
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
