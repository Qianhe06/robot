#!/usr/bin/env python3
"""Run and stop the search through direct ROS 2 service clients."""

import time
import signal

from diagnostic_msgs.msg import DiagnosticArray
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_srvs.srv import SetBool


class SearchConsole(Node):
    def __init__(self):
        super().__init__("go2_search_console")
        self.search_client = self.create_client(
            SetBool, "/scissors_search/enable",
        )
        self.motion_client = self.create_client(
            SetBool, "/go2_motion/enable",
        )
        qos = QoSProfile(depth=1)
        qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
        qos.reliability = ReliabilityPolicy.RELIABLE
        self.create_subscription(
            DiagnosticArray, "/scissors_search/status",
            self.status_callback, qos,
        )
        self.last_status = None
        self.finished = False

    def status_callback(self, message):
        if not message.status:
            return
        status = message.status[0]
        detail = next(
            (item.value for item in status.values if item.key == "detail"),
            "",
        )
        current = (status.message, detail)
        if current != self.last_status:
            print(f"{status.message}: {detail}", flush=True)
            self.last_status = current
        self.finished = status.message in (
            "FOUND", "NOT_FOUND", "FAULT", "CANCELLED",
        )

    def set_enabled(self, client, enabled, timeout=5.0):
        if not client.wait_for_service(timeout_sec=timeout):
            raise RuntimeError(f"{client.srv_name} 不可用；请先启动自主系统")
        request = SetBool.Request()
        request.data = enabled
        future = client.call_async(request)
        deadline = time.monotonic() + timeout
        while rclpy.ok() and not future.done():
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise TimeoutError(f"{client.srv_name} 调用超时")
            rclpy.spin_once(self, timeout_sec=min(0.1, remaining))
        response = future.result()
        if response is None or not response.success:
            reason = "无响应" if response is None else response.message
            raise RuntimeError(f"{client.srv_name}: {reason}")
        print(response.message, flush=True)


def main():
    rclpy.init()
    node = SearchConsole()
    started = False
    stop_requested = False

    def request_stop(signum, frame):
        nonlocal stop_requested
        del signum, frame
        stop_requested = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        node.set_enabled(node.search_client, True, timeout=8.0)
        started = True
        print("任务运行中；按 Ctrl+C 取消并禁用运动。", flush=True)
        while rclpy.ok() and not node.finished and not stop_requested:
            rclpy.spin_once(node, timeout_sec=0.2)
    except KeyboardInterrupt:
        print("收到停止指令。", flush=True)
    except Exception as error:
        print(f"搜索终止：{error}", flush=True)
    finally:
        if rclpy.ok():
            if started:
                try:
                    node.set_enabled(node.search_client, False)
                except Exception as error:
                    print(f"取消任务失败：{error}", flush=True)
            try:
                node.set_enabled(node.motion_client, False)
            except Exception as error:
                print(f"禁用运动失败：{error}", flush=True)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
