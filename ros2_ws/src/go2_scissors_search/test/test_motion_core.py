"""Tests for velocity limiting, Unitree payloads, and safety watchdogs."""

import json

import pytest

from go2_scissors_search.motion_core import limit_velocity
from go2_scissors_search.motion_core import MotionSafetyController
from go2_scissors_search.motion_core import obstacle_enable_payload
from go2_scissors_search.motion_core import obstacle_move_payload
from go2_scissors_search.motion_core import remote_api_payload
from go2_scissors_search.motion_core import REQUIRED_STREAMS
from go2_scissors_search.motion_core import VelocityCommand


def _healthy_controller(now=10.0):
    controller = MotionSafetyController()
    for stream in ("odom", "cloud", "camera", "interface"):
        controller.update_stream(stream, now)
    return controller


def test_velocity_limit_preserves_planar_direction():
    limited = limit_velocity(VelocityCommand(0.3, 0.4, 0.9))

    assert limited.x == pytest.approx(0.15)
    assert limited.y == pytest.approx(0.20)
    assert limited.yaw == pytest.approx(0.40)


def test_unitree_move_payload_uses_velocity_mode():
    payload = json.loads(obstacle_move_payload(
        VelocityCommand(0.2, -0.1, 0.3)
    ))

    assert payload == {"mode": 0, "x": 0.2, "y": -0.1, "yaw": 0.3}


def test_unitree_configuration_payloads_enable_avoidance_and_api_control():
    assert json.loads(obstacle_enable_payload()) == {"enable": True}
    assert json.loads(remote_api_payload()) == {
        "is_remote_commands_from_api": True,
    }


def test_enable_requires_every_stream():
    controller = MotionSafetyController()
    controller.update_stream("odom", 10.0)

    success, reason = controller.enable(10.0)

    assert not success
    assert "cloud" in reason
    assert "camera" in reason
    assert "interface" in reason


def test_fresh_command_is_forwarded_when_enabled():
    controller = _healthy_controller()
    success, _ = controller.enable(10.0)
    controller.set_command(VelocityCommand(0.1, 0.0, -0.2), 10.1)

    decision = controller.evaluate(10.2)

    assert success
    assert decision.enabled
    assert not decision.locked
    assert decision.command == VelocityCommand(0.1, 0.0, -0.2)


def test_command_timeout_latches_stop():
    controller = _healthy_controller()
    controller.enable(10.0)
    controller.set_command(VelocityCommand(0.1, 0.0, 0.0), 10.0)

    decision = controller.evaluate(10.31)

    assert not decision.enabled
    assert decision.locked
    assert decision.command == VelocityCommand()
    assert decision.fault_reason == "velocity command timeout"


def test_stale_sensor_requires_explicit_reenable():
    controller = _healthy_controller()
    controller.enable(10.0)
    controller.set_command(VelocityCommand(0.1, 0.0, 0.0), 10.1)
    controller.update_stream("interface", 10.6)
    controller.update_stream("camera", 10.6)

    decision = controller.evaluate(10.6)

    assert decision.locked
    assert "odom" in decision.fault_reason
    controller.update_stream("odom", 10.6)
    controller.update_stream("cloud", 10.6)
    assert not controller.evaluate(10.6).enabled
    assert controller.enable(10.6)[0]


@pytest.mark.parametrize("stale_stream", REQUIRED_STREAMS)
def test_each_required_stream_has_a_latching_watchdog(stale_stream):
    controller = MotionSafetyController(
        odom_timeout=0.5,
        cloud_timeout=0.5,
        camera_timeout=0.5,
        interface_timeout=0.5,
    )
    for stream in REQUIRED_STREAMS:
        controller.update_stream(stream, 1.0)
    controller.enable(1.0)
    controller.set_command(VelocityCommand(0.1, 0.0, 0.0), 1.6)
    for stream in REQUIRED_STREAMS:
        if stream != stale_stream:
            controller.update_stream(stream, 1.6)

    decision = controller.evaluate(1.6)

    assert decision.locked
    assert stale_stream in decision.fault_reason
