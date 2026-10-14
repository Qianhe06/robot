"""Hardware-independent safety logic for the Unitree motion adapter."""

from dataclasses import dataclass
import json
import math


REQUIRED_STREAMS = ("odom", "cloud", "camera", "interface")


@dataclass(frozen=True)
class VelocityCommand:
    """A planar velocity command in metres and radians per second."""

    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0


@dataclass(frozen=True)
class SafetyDecision:
    """The command that may be sent and any latched fault reason."""

    command: VelocityCommand
    enabled: bool
    locked: bool
    fault_reason: str | None


def limit_velocity(command, max_linear=0.25, max_angular=0.40):
    """Limit planar magnitude and yaw rate while preserving direction."""
    values = (float(command.x), float(command.y), float(command.yaw))
    if not all(math.isfinite(value) for value in values):
        raise ValueError("velocity command must contain finite values")
    if max_linear <= 0.0 or max_angular <= 0.0:
        raise ValueError("velocity limits must be positive")

    x, y, yaw = values
    magnitude = math.hypot(x, y)
    if magnitude > max_linear:
        scale = max_linear / magnitude
        x *= scale
        y *= scale
    yaw = max(-max_angular, min(max_angular, yaw))
    return VelocityCommand(x=x, y=y, yaw=yaw)


def obstacle_move_payload(command):
    """Build the documented Unitree obstacle-avoidance velocity payload."""
    return json.dumps(
        {
            "mode": 0,
            "x": float(command.x),
            "y": float(command.y),
            "yaw": float(command.yaw),
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def obstacle_enable_payload(enabled=True):
    """Build the obstacle-avoidance switch payload."""
    return json.dumps(
        {"enable": bool(enabled)},
        separators=(",", ":"),
        sort_keys=True,
    )


def remote_api_payload(enabled=True):
    """Build the payload selecting API commands as the remote source."""
    return json.dumps(
        {"is_remote_commands_from_api": bool(enabled)},
        separators=(",", ":"),
        sort_keys=True,
    )


class MotionSafetyController:
    """Latch motion off whenever a required data stream becomes stale."""

    def __init__(
        self,
        max_linear=0.25,
        max_angular=0.40,
        command_timeout=0.30,
        odom_timeout=0.50,
        cloud_timeout=0.50,
        camera_timeout=1.00,
        interface_timeout=1.00,
    ):
        self.max_linear = float(max_linear)
        self.max_angular = float(max_angular)
        self.command_timeout = float(command_timeout)
        self.stream_timeouts = {
            "odom": float(odom_timeout),
            "cloud": float(cloud_timeout),
            "camera": float(camera_timeout),
            "interface": float(interface_timeout),
        }
        if self.command_timeout <= 0.0 or any(
            timeout <= 0.0 for timeout in self.stream_timeouts.values()
        ):
            raise ValueError("watchdog timeouts must be positive")

        self._stream_times = {name: None for name in REQUIRED_STREAMS}
        self._command_time = None
        self._command = VelocityCommand()
        self.enabled = False
        self.locked = False
        self.fault_reason = None

    def update_stream(self, name, timestamp):
        """Record arrival of a required stream at a monotonic timestamp."""
        if name not in self._stream_times:
            raise ValueError(f"unknown stream: {name}")
        value = float(timestamp)
        if not math.isfinite(value):
            raise ValueError("timestamp must be finite")
        self._stream_times[name] = value

    def set_command(self, command, timestamp):
        """Store a new limited command and its arrival timestamp."""
        value = float(timestamp)
        if not math.isfinite(value):
            raise ValueError("timestamp must be finite")
        self._command = limit_velocity(
            command,
            max_linear=self.max_linear,
            max_angular=self.max_angular,
        )
        self._command_time = value
        return self._command

    def stale_streams(self, now):
        """Return names of required streams that are absent or stale."""
        current = float(now)
        stale = []
        for name, timeout in self.stream_timeouts.items():
            timestamp = self._stream_times[name]
            if timestamp is None or current - timestamp > timeout:
                stale.append(name)
        return stale

    def enable(self, now):
        """Enable motion only when every required stream is currently fresh."""
        stale = self.stale_streams(now)
        if stale:
            self.enabled = False
            self.fault_reason = "stale streams: " + ", ".join(stale)
            return False, self.fault_reason
        self.enabled = True
        self.locked = False
        self.fault_reason = None
        self._command = VelocityCommand()
        self._command_time = float(now)
        return True, "motion enabled"

    def disable(self, reason="motion disabled"):
        """Stop without latching a fault."""
        self.enabled = False
        self._command = VelocityCommand()
        self.fault_reason = str(reason)

    def emergency_stop(self, reason="emergency stop"):
        """Stop immediately and require an explicit successful re-enable."""
        self.enabled = False
        self.locked = True
        self._command = VelocityCommand()
        self.fault_reason = str(reason)

    def evaluate(self, now):
        """Apply all watchdogs and return the only command safe to publish."""
        current = float(now)
        if not self.enabled:
            return SafetyDecision(
                command=VelocityCommand(),
                enabled=False,
                locked=self.locked,
                fault_reason=self.fault_reason,
            )

        stale = self.stale_streams(current)
        if stale:
            self.emergency_stop("stale streams: " + ", ".join(stale))
        elif (
            self._command_time is None
            or current - self._command_time > self.command_timeout
        ):
            self.emergency_stop("velocity command timeout")

        command = self._command if self.enabled else VelocityCommand()
        return SafetyDecision(
            command=command,
            enabled=self.enabled,
            locked=self.locked,
            fault_reason=self.fault_reason,
        )
