import math
from dataclasses import dataclass
from itertools import count
from threading import Lock
from typing import Any, Callable, Generic, Protocol, TypeVar


ResultType = TypeVar("ResultType")


class TaskError(RuntimeError):
    pass


class TaskUnavailableError(TaskError):
    pass


class TaskInactiveError(TaskError):
    pass


class TaskQueueFullError(TaskError):
    pass


class TaskCancelledError(TaskError):
    pass


@dataclass(frozen=True)
class TaskResult(Generic[ResultType]):
    value: ResultType | None = None
    error: BaseException | None = None

    def unwrap(self) -> ResultType:
        if self.error is not None:
            raise self.error

        return self.value


class _TaskState:
    def __init__(self, active):
        self.lock = Lock()
        self.active = active
        self.running = False
        self.done = False
        self.cancelled = False
        self.completion_pending = False
        self.result = None


class CancellationToken:
    def __init__(self, state: _TaskState):
        self._state = state

    @property
    def cancelled(self) -> bool:
        with self._state.lock:
            if self._state.cancelled:
                return True

            if self._state.done:
                return False

        return not self._state.active()

    def throw_if_cancelled(self) -> None:
        if self.cancelled:
            raise TaskCancelledError("Managed task was cancelled")


class TaskHandle(Generic[ResultType]):
    def __init__(self, owner, task_id: int, periodic: bool):
        self._owner = owner
        self._task_id = task_id
        self._periodic = periodic
        self._state = _TaskState(lambda: owner._task_active(task_id))
        self._token = CancellationToken(self._state)

    @property
    def token(self) -> CancellationToken:
        return self._token

    @property
    def done(self) -> bool:
        with self._state.lock:
            return self._state.done

    @property
    def cancelled(self) -> bool:
        with self._state.lock:
            return self._state.cancelled

    @property
    def result(self) -> TaskResult[ResultType]:
        with self._state.lock:
            if self._state.cancelled:
                raise TaskCancelledError("Managed task was cancelled")

            if not self._state.done:
                raise RuntimeError("Managed task has not completed")

            return self._state.result

    def cancel(self) -> bool:
        with self._state.lock:
            if self._state.done and not self._state.completion_pending:
                return False

            self._state.cancelled = True
            self._state.done = True
            self._state.completion_pending = False

        self._owner._cancel(self._task_id)
        return True

    def _start(self) -> bool:
        with self._state.lock:
            if self._state.cancelled or self._state.done or self._state.running:
                return False

            self._state.running = True
            return True

    def _finish(
            self,
            result: TaskResult[ResultType],
            has_completion: bool,
    ) -> tuple[bool, bool]:
        with self._state.lock:
            self._state.running = False

            if self._state.cancelled or self._state.done:
                return False, False

            if self._periodic and result.error is None:
                return False, True

            self._state.done = True
            self._state.result = result
            self._state.completion_pending = has_completion
            return has_completion, False

    def _take_completion(self):
        with self._state.lock:
            if self._state.cancelled or not self._state.completion_pending:
                return None

            self._state.completion_pending = False
            return self._state.result

    def _reject(self, error: BaseException) -> None:
        with self._state.lock:
            if self._state.done:
                return

            self._state.done = True
            self._state.result = TaskResult(error=error)

    def _replaceable(self) -> bool:
        with self._state.lock:
            return not self._state.running and not self._state.done


class TasksPort(Protocol):
    def bind_runtime(self, host: Any, generation: int) -> None:
        ...

    def schedule(
            self,
            task_id: int,
            delay_ms: int,
            interval_ms: int,
            callback: Callable[[], bool],
            completion: Callable[[], bool] | None,
    ) -> bool | None:
        ...

    def cancel(self, task_id: int) -> None:
        ...

    def active(self, task_id: int) -> bool:
        ...

    def close(self) -> None:
        ...


class Tasks:
    MAX_TASKS = 128
    MAX_DELAY_SECONDS = 7 * 24 * 60 * 60
    MIN_PERIOD_SECONDS = 1

    def __init__(self, port: TasksPort | None):
        self._port = port
        self._host = None
        self._generation = None
        self._closed = False
        self._closing = False
        self._lock = Lock()
        self._key_lock = Lock()
        self._ids = count(1)
        self._records = {}
        self._keyed = {}

    def _bind_runtime(self, host: Any, generation: int) -> None:
        with self._lock:
            if self._closed or self._host is not None:
                raise TaskInactiveError("Managed tasks are inactive")

            self._host = host
            self._generation = generation

        if self._port is not None:
            self._port.bind_runtime(host, generation)

    def submit(
            self,
            operation: Callable[[CancellationToken], ResultType],
            *,
            on_complete: Callable[[TaskResult[ResultType]], Any] | None = None,
    ) -> TaskHandle[ResultType]:
        return self._schedule(operation, 0, 0, on_complete=on_complete)

    def delay(
            self,
            delay: float,
            operation: Callable[[CancellationToken], ResultType],
            *,
            on_complete: Callable[[TaskResult[ResultType]], Any] | None = None,
    ) -> TaskHandle[ResultType]:
        return self._schedule(
            operation,
            self._milliseconds(delay, allow_zero=True),
            0,
            on_complete=on_complete,
        )

    def periodic(
            self,
            interval: float,
            operation: Callable[[CancellationToken], Any],
            *,
            initial_delay: float | None = None,
    ) -> TaskHandle[Any]:
        interval_ms = self._milliseconds(interval, allow_zero=False)

        if interval < self.MIN_PERIOD_SECONDS:
            raise ValueError("Periodic task interval must be at least one second")

        delay_ms = interval_ms if initial_delay is None else self._milliseconds(
            initial_delay,
            allow_zero=True,
        )
        return self._schedule(operation, delay_ms, interval_ms)

    def debounce(
            self,
            key: str,
            delay: float,
            operation: Callable[[CancellationToken], ResultType],
            *,
            on_complete: Callable[[TaskResult[ResultType]], Any] | None = None,
    ) -> TaskHandle[ResultType]:
        key = self._key(key)

        with self._key_lock:
            previous = self._keyed_handle("debounce", key)

            if previous is not None:
                previous.cancel()

            return self._schedule(
                operation,
                self._milliseconds(delay, allow_zero=True),
                0,
                key=("debounce", key),
                on_complete=on_complete,
            )

    def coalesce(
            self,
            key: str,
            operation: Callable[[CancellationToken], ResultType],
            *,
            on_complete: Callable[[TaskResult[ResultType]], Any] | None = None,
    ) -> TaskHandle[ResultType]:
        key = self._key(key)

        with self._key_lock:
            current = self._keyed_handle("coalesce", key)

            if current is not None and current._replaceable():
                with self._lock:
                    record = self._records.get(current._task_id)

                    if record is not None and current._replaceable():
                        record["operation"] = operation
                        record["on_complete"] = on_complete
                        return current

            return self._schedule(
                operation,
                0,
                0,
                key=("coalesce", key),
                on_complete=on_complete,
            )

    def _schedule(
            self,
            operation,
            delay_ms: int,
            interval_ms: int,
            *,
            key=None,
            on_complete=None,
    ):
        if not callable(operation):
            raise TypeError("operation must be callable")

        if on_complete is not None and not callable(on_complete):
            raise TypeError("on_complete must be callable")

        port = self._require_port()

        with self._lock:
            self._require_active()

            if len(self._records) >= self.MAX_TASKS:
                raise TaskQueueFullError("Managed task limit exceeded")

            task_id = next(self._ids)
            handle = TaskHandle(self, task_id, interval_ms > 0)
            self._records[task_id] = {
                "handle": handle,
                "operation": operation,
                "on_complete": on_complete,
                "key": key,
            }

            if key is not None:
                self._keyed[key] = handle

        submitted = port.schedule(
            task_id,
            delay_ms,
            interval_ms,
            lambda: self._run(task_id),
            None if on_complete is None else lambda: self._complete(task_id),
        )

        if not submitted:
            error = (
                TaskInactiveError("Managed tasks are inactive")
                if submitted is None
                else TaskQueueFullError("Managed task queue is full")
            )
            handle._reject(error)
            self._forget(task_id)

        return handle

    def _run(self, task_id: int) -> bool:
        with self._lock:
            record = self._records.get(task_id)

        if record is None or not self._is_active():
            return False

        handle = record["handle"]

        if not handle._start():
            self._forget(task_id)
            return False

        try:
            result = TaskResult(value=record["operation"](handle.token))
        except BaseException as error:
            result = TaskResult(error=error)

        if not self._is_active():
            handle.cancel()
            return False

        deliver, keep = handle._finish(
            result,
            record["on_complete"] is not None,
        )

        if not keep and not deliver:
            self._forget(task_id)

        return keep

    def _complete(self, task_id: int) -> bool:
        with self._lock:
            record = self._records.get(task_id)

        if record is None or not self._is_active():
            return False

        result = record["handle"]._take_completion()

        if result is None:
            self._forget(task_id)
            return False

        try:
            record["on_complete"](result)
        finally:
            self._forget(task_id)

        return False

    def _cancel(self, task_id: int) -> None:
        port = self._port
        self._forget(task_id)

        if port is not None:
            port.cancel(task_id)

    def _task_active(self, task_id: int) -> bool:
        port = self._port
        return port is not None and self._is_active() and port.active(task_id)

    def _forget(self, task_id: int) -> None:
        with self._lock:
            record = self._records.pop(task_id, None)

            if record is None:
                return

            key = record["key"]

            if key is not None and self._keyed.get(key) is record["handle"]:
                self._keyed.pop(key, None)

    def _keyed_handle(self, kind: str, key: str):
        with self._lock:
            return self._keyed.get((kind, key))

    def _close(self) -> None:
        with self._lock:
            if self._closed:
                return

            self._closing = True
            handles = tuple(record["handle"] for record in self._records.values())

        for handle in handles:
            handle.cancel()

        if self._port is not None:
            self._port.close()

        with self._lock:
            self._records.clear()
            self._keyed.clear()
            self._closed = True
            self._closing = False
            self._host = None
            self._generation = None

    def _require_port(self) -> TasksPort:
        if self._port is None:
            raise TaskUnavailableError("Managed tasks are unavailable in this runtime")

        return self._port

    def _require_active(self) -> None:
        if not self._is_active():
            raise TaskInactiveError("Managed tasks are inactive")

    def _is_active(self) -> bool:
        return (
            not self._closed
            and not self._closing
            and self._host is not None
            and self._host.isGenerationActive(self._generation)
        )

    @classmethod
    def _milliseconds(cls, value: float, *, allow_zero: bool) -> int:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise TypeError("Task delay must be a finite number")

        minimum = 0 if allow_zero else 0

        if value < minimum or value > cls.MAX_DELAY_SECONDS:
            raise ValueError("Task delay is outside the supported range")

        return int(math.ceil(value * 1000))

    @staticmethod
    def _key(value: str) -> str:
        if not isinstance(value, str) or not value or len(value) > 128:
            raise ValueError("Task key must contain 1 to 128 characters")

        return value
