import json
import math
import re
from dataclasses import dataclass
from threading import Lock
from typing import Any, Generic, Protocol, TypeVar


class StorageError(RuntimeError):
    pass


class StorageUnavailableError(StorageError):
    pass


class StorageInactiveError(StorageError):
    pass


class StorageAccountUnavailableError(StorageError):
    pass


class StorageOwnerChangedError(StorageError):
    pass


class StorageThreadError(StorageError):
    pass


class StorageTypeError(StorageError):
    pass


class StorageQuotaExceededError(StorageError):
    pass


class StorageTransactionError(StorageError):
    pass


class StorageQueueFullError(StorageError):
    pass


class StorageMigrationError(StorageError):
    pass


@dataclass(frozen=True)
class StorageLimits:
    max_entries: int = 4096
    max_value_bytes: int = 256 * 1024
    max_total_value_bytes: int = 4 * 1024 * 1024


@dataclass(frozen=True)
class StorageStatistics:
    value_count: int
    value_bytes: int
    schema_version: int
    limits: StorageLimits


ResultType = TypeVar("ResultType")


@dataclass(frozen=True)
class StorageResult(Generic[ResultType]):
    value: ResultType | None = None
    error: BaseException | None = None

    def unwrap(self) -> ResultType:
        if self.error is not None:
            raise self.error

        return self.value


class StorageTask(Generic[ResultType]):
    def __init__(self):
        self._lock = Lock()
        self._started = False
        self._done = False
        self._cancelled = False
        self._result = None

    @property
    def done(self) -> bool:
        with self._lock:
            return self._done

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    @property
    def result(self) -> StorageResult[ResultType]:
        with self._lock:
            if self._cancelled:
                raise RuntimeError("Storage task was cancelled")

            if not self._done:
                raise RuntimeError("Storage task has not completed")

            return self._result

    def cancel(self) -> bool:
        with self._lock:
            if self._done:
                return False

            self._cancelled = True
            self._done = True

            return True

    def _start(self) -> bool:
        with self._lock:
            if self._cancelled or self._done:
                return False

            self._started = True
            return True

    def _finish(self, result: StorageResult[ResultType]) -> bool:
        with self._lock:
            self._done = True

            if self._cancelled:
                return False

            self._result = result
            return True

    def _reject(self, error: BaseException) -> None:
        with self._lock:
            self._done = True
            self._result = StorageResult(error=error)


@dataclass(frozen=True)
class _StoredValue:
    kind: str
    payload: Any
    byte_size: int


@dataclass(frozen=True)
class _StorageOperation:
    action: str
    scope: Any
    key: str | None = None
    value: _StoredValue | None = None


class StoragePort(Protocol):
    def bind_runtime(self, host: Any, generation: int) -> None:
        ...

    def close(self) -> None:
        ...

    def global_scope(self) -> Any:
        ...

    def account_scope(self, account: int) -> Any:
        ...

    def read(self, scope: Any, key: str) -> _StoredValue | None:
        ...

    def apply(
            self,
            operations: tuple[_StorageOperation, ...],
            schema_version: int | None = None,
    ) -> None:
        ...

    def statistics(self) -> StorageStatistics:
        ...

    def submit(self, callback) -> bool | None:
        ...

    def complete(self, callback) -> None:
        ...


_MISSING = object()
_DELETED = object()
_KEY_PATTERN = re.compile(r"[a-z][a-z0-9_.-]{0,127}")


def _validate_key(key: str) -> str:
    if (
            not isinstance(key, str)
            or _KEY_PATTERN.fullmatch(key) is None
            or len(key.encode("utf-8")) > 128
    ):
        raise ValueError("Invalid storage key")

    return key


def _validate_int(value: int) -> int:
    if type(value) is not int:
        raise TypeError("Storage integer must be an int")

    if not -(2**63) <= value <= 2**63 - 1:
        raise ValueError("Storage integer is out of signed 64-bit range")

    return value


def _validate_json(value: Any, depth: int = 0) -> None:
    if depth > 32:
        raise ValueError("Storage JSON exceeds maximum depth")

    if value is None or type(value) in (bool, str):
        return

    if type(value) is int:
        _validate_int(value)
        return

    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Storage JSON numbers must be finite")
        return

    if isinstance(value, list):
        for item in value:
            _validate_json(item, depth + 1)
        return

    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("Storage JSON object keys must be strings")
            _validate_json(item, depth + 1)
        return

    raise TypeError("Unsupported storage JSON value")


def _stored(kind: str, payload: Any) -> _StoredValue:
    if kind == "bool":
        if type(payload) is not bool:
            raise TypeError("Storage boolean must be a bool")
        size = 1
    elif kind == "int":
        payload = _validate_int(payload)
        size = 8
    elif kind == "float":
        if type(payload) is not float or not math.isfinite(payload):
            raise ValueError("Storage float must be finite")
        size = 8
    elif kind == "string":
        if not isinstance(payload, str):
            raise TypeError("Storage string must be a str")
        size = len(payload.encode("utf-8"))
    elif kind == "bytes":
        if not isinstance(payload, bytes):
            raise TypeError("Storage bytes must be bytes")
        payload = bytes(payload)
        size = len(payload)
    elif kind == "json":
        _validate_json(payload)
        payload = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        size = len(payload.encode("utf-8"))
    else:
        raise StorageTypeError(f"Unsupported storage type: {kind}")

    return _StoredValue(kind, payload, size)


def _decode(value: _StoredValue, expected: str) -> Any:
    if value.kind != expected:
        raise StorageTypeError(
            f"Stored value has type {value.kind}, expected {expected}"
        )

    if expected == "bytes":
        return bytes(value.payload)

    if expected == "json":
        return json.loads(value.payload)

    return value.payload


class StorageScope:
    def __init__(self, storage, scope: Any, transaction=None):
        self._storage = storage
        self._scope = scope
        self._transaction = transaction

    def contains(self, key: str) -> bool:
        return self._read(_validate_key(key)) is not None

    def delete(self, key: str) -> bool:
        key = _validate_key(key)
        existed = self._read(key) is not None
        self._write(key, _DELETED)

        return existed

    def clear(self) -> None:
        if self._transaction is not None:
            self._transaction._clear(self._scope)
            return

        self._storage._require_no_transaction()
        self._storage._apply((_StorageOperation("clear", self._scope),))

    def get_bool(self, key: str, default: Any = _MISSING) -> bool:
        return self._get(key, "bool", default)

    def set_bool(self, key: str, value: bool) -> None:
        self._write(_validate_key(key), _stored("bool", value))

    def get_int(self, key: str, default: Any = _MISSING) -> int:
        return self._get(key, "int", default)

    def set_int(self, key: str, value: int) -> None:
        self._write(_validate_key(key), _stored("int", value))

    def get_float(self, key: str, default: Any = _MISSING) -> float:
        return self._get(key, "float", default)

    def set_float(self, key: str, value: float) -> None:
        self._write(_validate_key(key), _stored("float", value))

    def get_string(self, key: str, default: Any = _MISSING) -> str:
        return self._get(key, "string", default)

    def set_string(self, key: str, value: str) -> None:
        self._write(_validate_key(key), _stored("string", value))

    def get_bytes(self, key: str, default: Any = _MISSING) -> bytes:
        return self._get(key, "bytes", default)

    def set_bytes(self, key: str, value: bytes) -> None:
        self._write(_validate_key(key), _stored("bytes", value))

    def get_json(self, key: str, default: Any = _MISSING) -> Any:
        value = self._read(_validate_key(key))

        if value is None:
            if default is _MISSING:
                raise KeyError(key)

            if default is None:
                return None

            return _decode(_stored("json", default), "json")

        return _decode(value, "json")

    def set_json(self, key: str, value: Any) -> None:
        self._write(_validate_key(key), _stored("json", value))

    def _get(self, key: str, expected: str, default: Any) -> Any:
        value = self._read(_validate_key(key))

        if value is None:
            if default is _MISSING:
                raise KeyError(key)

            if default is None:
                return None

            return _decode(_stored(expected, default), expected)

        return _decode(value, expected)

    def _read(self, key: str) -> _StoredValue | None:
        if self._transaction is not None:
            return self._transaction._read(self._scope, key)

        self._storage._require_no_transaction()
        return self._storage._read(self._scope, key)

    def _write(self, key: str, value: _StoredValue | object) -> None:
        if self._transaction is not None:
            self._transaction._write(self._scope, key, value)
            return

        self._storage._require_no_transaction()
        action = "delete" if value is _DELETED else "set"
        operation = _StorageOperation(
            action,
            self._scope,
            key,
            None if value is _DELETED else value,
        )
        self._storage._apply((operation,))


class StorageTransaction:
    def __init__(self, storage):
        self._storage = storage
        self._changes = OrderedChanges()
        self._cleared = set()
        self._entered = False
        self._closed = False
        self._schema_version = None

    @property
    def global_scope(self) -> StorageScope:
        self._require_open()
        return StorageScope(self._storage, self._storage._global_scope(), self)

    def account(self, account: int) -> StorageScope:
        self._require_open()
        return StorageScope(self._storage, self._storage._account_scope(account), self)

    def __enter__(self):
        if self._entered or self._closed:
            raise StorageTransactionError("Storage transaction cannot be reused")

        self._storage._begin_transaction(self)
        self._entered = True

        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is None:
                self._commit()
        finally:
            self._closed = True
            self._storage._finish_transaction(self)

        return False

    def _read(self, scope: Any, key: str) -> _StoredValue | None:
        self._require_open()
        change = self._changes.get(scope, key, _MISSING)

        if change is _DELETED:
            return None

        if change is not _MISSING:
            return change

        if scope in self._cleared:
            return None

        return self._storage._read(scope, key)

    def _write(self, scope: Any, key: str, value: _StoredValue | object) -> None:
        self._require_open()
        self._changes.set(scope, key, value)

    def _clear(self, scope: Any) -> None:
        self._require_open()
        self._cleared.add(scope)
        self._changes.remove_scope(scope)

    def _commit(self) -> None:
        operations = [
            _StorageOperation("clear", scope)
            for scope in self._cleared
        ]

        for scope, key, value in self._changes.items():
            operations.append(_StorageOperation(
                "delete" if value is _DELETED else "set",
                scope,
                key,
                None if value is _DELETED else value,
            ))

        self._storage._apply(tuple(operations), self._schema_version)

    def _require_open(self) -> None:
        if not self._entered or self._closed:
            raise StorageTransactionError("Storage transaction is not active")


class OrderedChanges:
    def __init__(self):
        self._values = {}
        self._order = []

    def set(self, scope: Any, key: str, value: Any) -> None:
        pair = (scope, key)

        if pair not in self._values:
            self._order.append(pair)

        self._values[pair] = value

    def get(self, scope: Any, key: str, default: Any) -> Any:
        return self._values.get((scope, key), default)

    def remove_scope(self, scope: Any) -> None:
        for pair in tuple(self._order):
            if pair[0] == scope:
                self._order.remove(pair)
                self._values.pop(pair, None)

    def items(self):
        for scope, key in self._order:
            yield scope, key, self._values[(scope, key)]


class StorageMigration:
    def __init__(self, storage, from_version: int, to_version: int):
        if type(from_version) is not int or type(to_version) is not int:
            raise TypeError("Storage schema versions must be integers")

        if from_version < 0 or to_version < from_version:
            raise StorageMigrationError("Invalid storage migration versions")

        self.from_version = from_version
        self.to_version = to_version
        self._transaction = StorageTransaction(storage)

    @property
    def global_scope(self) -> StorageScope:
        return self._transaction.global_scope

    def account(self, account: int) -> StorageScope:
        return self._transaction.account(account)

    def __enter__(self):
        self._transaction.__enter__()
        self._transaction._schema_version = self.to_version

        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return self._transaction.__exit__(exc_type, exc_value, traceback)
        except StorageError:
            raise
        except BaseException as error:
            raise StorageMigrationError("Storage migration commit failed") from error


class Storage:
    def __init__(self, port: StoragePort | None):
        self._port = port
        self._closed = False
        self._port_closed = False
        self._transaction = None
        self._tasks = set()

    def _bind_runtime(self, host: Any, generation: int) -> None:
        if self._port is not None:
            self._port.bind_runtime(host, generation)

    def _close(self) -> None:
        if self._port_closed:
            return

        self._closed = True

        for task in tuple(self._tasks):
            task.cancel()

        self._tasks.clear()

        if self._port is None:
            self._port_closed = True
            return

        self._port.close()
        self._port_closed = True

    @property
    def global_scope(self) -> StorageScope:
        return StorageScope(self, self._global_scope())

    def account(self, account: int) -> StorageScope:
        return StorageScope(self, self._account_scope(account))

    def transaction(self) -> StorageTransaction:
        self._require_port()
        return StorageTransaction(self)

    def statistics(self) -> StorageStatistics:
        return self._require_port().statistics()

    def clear_all(self) -> None:
        self._require_no_transaction()
        self._apply((_StorageOperation("clear_all", None),))

    def submit(
            self,
            operation,
            *,
            on_complete=None,
    ) -> StorageTask:
        if not callable(operation):
            raise TypeError("operation must be callable")

        if on_complete is not None and not callable(on_complete):
            raise TypeError("on_complete must be callable")

        port = self._require_port()
        task = StorageTask()
        self._tasks.add(task)

        def run():
            if not task._start():
                self._tasks.discard(task)
                return

            try:
                result = StorageResult(value=operation(self))
            except BaseException as error:
                result = StorageResult(error=error)

            deliver = task._finish(result)

            if on_complete is None or not deliver:
                self._tasks.discard(task)
                return

            def complete():
                self._tasks.discard(task)

                if not self._closed and not task.cancelled:
                    on_complete(result)

            port.complete(complete)

        submitted = port.submit(run)

        if not submitted:
            error = (
                StorageInactiveError("Plugin storage is inactive")
                if submitted is None
                else StorageQueueFullError("Plugin storage task queue is full")
            )
            task._reject(error)
            self._tasks.discard(task)

            if submitted is not None and on_complete is not None:
                def complete_rejection():
                    if not self._closed and not task.cancelled:
                        on_complete(task.result)

                port.complete(complete_rejection)

        return task

    def _global_scope(self) -> Any:
        return self._require_port().global_scope()

    def _account_scope(self, account: int) -> Any:
        if type(account) is not int or account < 0:
            raise ValueError("account must be a non-negative integer")

        return self._require_port().account_scope(account)

    def _read(self, scope: Any, key: str) -> _StoredValue | None:
        return self._require_port().read(scope, key)

    def _apply(
            self,
            operations: tuple[_StorageOperation, ...],
            schema_version: int | None = None,
    ) -> None:
        self._require_port().apply(operations, schema_version)

    def _begin_transaction(self, transaction: StorageTransaction) -> None:
        self._require_port()

        if self._transaction is not None:
            raise StorageTransactionError("Nested storage transactions are not supported")

        self._transaction = transaction

    def _finish_transaction(self, transaction: StorageTransaction) -> None:
        if self._transaction is transaction:
            self._transaction = None

    def _require_no_transaction(self) -> None:
        if self._transaction is not None:
            raise StorageTransactionError(
                "Use scopes from the active storage transaction"
            )

    def _require_port(self) -> StoragePort:
        if self._closed:
            raise StorageInactiveError("Plugin storage is inactive")

        if self._port is None:
            raise StorageUnavailableError(
                "Plugin storage is not available in this client runtime"
            )

        return self._port
