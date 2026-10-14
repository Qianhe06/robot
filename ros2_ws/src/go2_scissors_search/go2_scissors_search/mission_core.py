"""Deterministic state machine for autonomous scissors search."""

from enum import Enum


class SearchState(str, Enum):
    """Public mission states reported on ``/scissors_search/status``."""

    IDLE = "IDLE"
    MAPPING = "MAPPING"
    EXPLORING = "EXPLORING"
    INSPECTING = "INSPECTING"
    CONFIRMING = "CONFIRMING"
    APPROACHING = "APPROACHING"
    FOUND = "FOUND"
    RETURNING = "RETURNING"
    NOT_FOUND = "NOT_FOUND"
    FAULT = "FAULT"
    CANCELLED = "CANCELLED"


class SearchMission:
    """Track legal mission transitions and repeated planning failures."""

    def __init__(self, frontier_exhaustion_limit=3, retry_limit=3):
        self.frontier_exhaustion_limit = int(frontier_exhaustion_limit)
        self.retry_limit = int(retry_limit)
        if self.frontier_exhaustion_limit < 1 or self.retry_limit < 1:
            raise ValueError("mission limits must be positive")
        self.state = SearchState.IDLE
        self.frontier_exhaustions = 0
        self.retries = 0

    def start(self):
        """Start mapping from an idle or terminal state."""
        self.frontier_exhaustions = 0
        self.retries = 0
        self.state = SearchState.MAPPING
        return self.state

    def map_ready(self):
        """Enter frontier exploration after the first usable map arrives."""
        if self.state == SearchState.MAPPING:
            self.state = SearchState.EXPLORING
        return self.state

    def frontier_exhausted(self):
        """Switch to wall inspection after repeated empty frontier plans."""
        if self.state != SearchState.EXPLORING:
            return self.state
        self.frontier_exhaustions += 1
        if self.frontier_exhaustions >= self.frontier_exhaustion_limit:
            self.state = SearchState.INSPECTING
        return self.state

    def frontier_available(self):
        """Clear consecutive frontier exhaustion evidence."""
        if self.state == SearchState.EXPLORING:
            self.frontier_exhaustions = 0
        return self.state

    def candidate_detected(self):
        """Pause search and begin stationary target confirmation."""
        if self.state in (SearchState.EXPLORING, SearchState.INSPECTING):
            self.state = SearchState.CONFIRMING
            self.retries = 0
        return self.state

    def target_confirmed(self):
        """Begin approach after target position confirmation."""
        if self.state == SearchState.CONFIRMING:
            self.state = SearchState.APPROACHING
        return self.state

    def approach_failed(self):
        """Reconfirm transient failures and resume search after the limit."""
        if self.state not in (SearchState.CONFIRMING, SearchState.APPROACHING):
            return self.state
        self.retries += 1
        if self.retries >= self.retry_limit:
            self.state = SearchState.EXPLORING
            self.frontier_exhaustions = 0
            self.retries = 0
        else:
            self.state = SearchState.CONFIRMING
        return self.state

    def target_reached(self):
        """Finish successfully at the target-facing standoff pose."""
        if self.state == SearchState.APPROACHING:
            self.state = SearchState.FOUND
        return self.state

    def inspection_complete(self):
        """Return to the recorded start after all inspection poses are used."""
        if self.state == SearchState.INSPECTING:
            self.state = SearchState.RETURNING
        return self.state

    def returned_without_target(self):
        """Finish with a clear not-found outcome after returning home."""
        if self.state == SearchState.RETURNING:
            self.state = SearchState.NOT_FOUND
        return self.state

    def cancel(self):
        """Cancel any active mission."""
        self.state = SearchState.CANCELLED
        return self.state

    def fault(self):
        """Latch a mission fault."""
        self.state = SearchState.FAULT
        return self.state
