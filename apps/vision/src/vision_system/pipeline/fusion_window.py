"""Time-windowed observation aggregation and rate-limited pose publishing."""

from __future__ import annotations

from collections import defaultdict, deque

from .detection import TagObservation
from .fusion import FusedPose, FusionEngine

NANOSECONDS_PER_MILLISECOND = 1_000_000
NANOSECONDS_PER_SECOND = 1_000_000_000


class ObservationWindow:
    """Buffers recent observations and feeds synchronized views to fusion."""

    def __init__(self, fusion: FusionEngine) -> None:
        self.fusion = fusion
        self.recent: dict[int, deque[TagObservation]] = defaultdict(deque)
        self.last_publish_ns: dict[int, int] = {}

    def clear(self) -> None:
        self.recent.clear()

    def add(self, observation: TagObservation) -> None:
        self.recent[observation.tag_id].append(observation)

    def fuse(self, updated_tags: set[int]) -> tuple[list[FusedPose], set[int]]:
        """Fuse every updated tag, reporting the ones fusion could not resolve."""
        poses: list[FusedPose] = []
        failed: set[int] = set()
        for tag_id in updated_tags:
            buffer = self.recent[tag_id]
            newest = max(item.monotonic_ns for item in buffer)
            cutoff = newest - int(
                self.fusion.config.window_ms * NANOSECONDS_PER_MILLISECOND
            )
            while buffer and buffer[0].monotonic_ns < cutoff:
                buffer.popleft()
            latest_per_camera: dict[str, TagObservation] = {}
            for observation in buffer:
                previous = latest_per_camera.get(observation.camera_id)
                if previous is None or observation.monotonic_ns > previous.monotonic_ns:
                    latest_per_camera[observation.camera_id] = observation
            pose = self.fusion.fuse(list(latest_per_camera.values()))
            if pose is None:
                failed.add(tag_id)
            else:
                poses.append(pose)
        return poses, failed

    def predict(
        self,
        fused_tags: set[int],
        monotonic_ns: int,
        utc_ns: int,
    ) -> list[FusedPose]:
        """Dead-reckon every tracked tag that this tick did not fuse.

        Tags whose fusion was rejected count as unfused, so a burst of
        contradictory observations coasts on the last trustworthy pose instead
        of leaving the tag without any output at all.
        """
        return [
            pose
            for tag_id in self.fusion.trackers.keys() - fused_tags
            if (pose := self.fusion.predict(tag_id, monotonic_ns, utc_ns)) is not None
        ]

    def should_publish(self, tag_id: int, monotonic_ns: int) -> bool:
        interval_ns = int(
            NANOSECONDS_PER_SECOND / self.fusion.config.publish_hz
        )
        return monotonic_ns - self.last_publish_ns.get(tag_id, 0) >= interval_ns

    def mark_published(self, tag_id: int, monotonic_ns: int) -> None:
        self.last_publish_ns[tag_id] = monotonic_ns
