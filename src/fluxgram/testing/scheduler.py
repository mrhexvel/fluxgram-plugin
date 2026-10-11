import heapq
import math
from collections import deque
from itertools import count


class FakeClock:
    def __init__(self):
        self._milliseconds = 0

    @property
    def milliseconds(self) -> int:
        return self._milliseconds

    def advance(self, seconds: float) -> None:
        if type(seconds) not in (int, float) or not math.isfinite(seconds):
            raise TypeError("Clock advance must be a finite number")

        if seconds < 0:
            raise ValueError("Clock cannot move backwards")

        self._milliseconds += int(math.ceil(seconds * 1000))


class HarnessTaskScheduler:
    MAX_TASKS = 128

    def __init__(self, clock: FakeClock | None = None):
        self.clock = clock or FakeClock()
        self._host = None
        self._generation = None
        self._closed = False
        self._foreground = True
        self._sequence = count()
        self._records = {}
        self._scheduled = []
        self._ready = deque()

    def bind_runtime(self, host, generation: int) -> None:
        if self._closed:
            raise RuntimeError("Managed task scheduler is closed")

        self._host = host
        self._generation = generation

    def schedule(self, task_id, delay_ms, interval_ms, callback, completion):
        if not self._active():
            return None

        if len(self._records) >= self.MAX_TASKS:
            return False

        record = {
            "callback": callback,
            "completion": completion,
            "interval": interval_ms,
            "resume_delay": delay_ms,
            "revision": 1,
            "running": False,
            "paused": interval_ms > 0 and not self._foreground,
        }
        self._records[task_id] = record

        if not record["paused"]:
            self._enqueue(task_id, delay_ms, record["revision"])

        return True

    def cancel(self, task_id) -> None:
        record = self._records.pop(task_id, None)

        if record is not None:
            record["revision"] += 1

    def active(self, task_id) -> bool:
        return task_id in self._records and self._active()

    def close(self) -> None:
        if self._closed:
            return

        self._closed = True
        self.invalidate()
        self._host = None
        self._generation = None

    def invalidate(self) -> None:
        for record in self._records.values():
            record["revision"] += 1

        self._records.clear()
        self._scheduled.clear()
        self._ready.clear()

    def advance(self, seconds: float) -> None:
        self.clock.advance(seconds)
        self._collect_due()

    def run_ready(self) -> None:
        while self.run_next():
            pass

    def run_next(self) -> bool:
        self._collect_due()

        while self._ready:
            task_id, revision = self._ready.popleft()
            record = self._records.get(task_id)

            if (
                    record is None
                    or record["revision"] != revision
                    or not self._active()
            ):
                continue

            record["running"] = True

            try:
                keep = bool(record["callback"]())
            except BaseException:
                self._records.pop(task_id, None)
                return True
            finally:
                record["running"] = False

            current = self._records.get(task_id)

            if current is not record or current["revision"] != revision:
                return True

            if record["interval"] <= 0 and record["completion"] is not None:
                record["callback"] = record["completion"]
                record["completion"] = None
                self._ready.append((task_id, revision))
                return True

            if record["interval"] <= 0 or not keep:
                self._records.pop(task_id, None)
                return True

            record["resume_delay"] = record["interval"]

            if self._foreground:
                self._enqueue(task_id, record["interval"], revision)
            else:
                record["paused"] = True

            return True

        return False

    def set_foreground(self, foreground: bool) -> None:
        if type(foreground) is not bool:
            raise TypeError("foreground must be a bool")

        if self._foreground == foreground:
            return

        self._foreground = foreground

        for task_id, record in tuple(self._records.items()):
            if record["interval"] <= 0 or record["running"]:
                continue

            record["revision"] += 1

            if foreground:
                record["paused"] = False
                self._enqueue(
                    task_id,
                    record["resume_delay"],
                    record["revision"],
                )
            else:
                record["paused"] = True

    @property
    def active_count(self) -> int:
        return len(self._records)

    @property
    def foreground(self) -> bool:
        return self._foreground

    def _enqueue(self, task_id: int, delay_ms: int, revision: int) -> None:
        if delay_ms == 0:
            self._ready.append((task_id, revision))
            return

        heapq.heappush(
            self._scheduled,
            (
                self.clock.milliseconds + delay_ms,
                next(self._sequence),
                task_id,
                revision,
            ),
        )

    def _collect_due(self) -> None:
        while self._scheduled and self._scheduled[0][0] <= self.clock.milliseconds:
            _, _, task_id, revision = heapq.heappop(self._scheduled)
            record = self._records.get(task_id)

            if record is not None and record["revision"] == revision:
                self._ready.append((task_id, revision))

    def _active(self) -> bool:
        return (
            not self._closed
            and self._host is not None
            and self._host.isGenerationActive(self._generation)
        )
