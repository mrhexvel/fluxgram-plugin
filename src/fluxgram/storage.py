import json
import math
import re
from dataclasses import dataclass
from threading import Lock
from typing import Any, Generic, Protocol, TypeVar


class StorageError(RuntimeError):
    """Base error for host-managed plugin storage operations."""

    pass


class StorageUnavailableError(StorageError):
    """Raised when the current runtime does not provide storage."""

    pass


class StorageInactiveError(StorageError):
    """Raised when storage is used outside its active plugin generation."""

    pass


class StorageAccountUnavailableError(StorageError):
    """Raised when an account-scoped operation has no active account owner."""

    pass


class StorageOwnerChangedError(StorageError):
    """Raised when an account scope no longer belongs to its captured owner."""

    pass


class StorageThreadError(StorageError):
    """Raised when direct storage I/O is attempted on the UI thread."""

    pass


class StorageTypeError(StorageError):
    """Raised when a stored value is read with a different typed accessor."""

    pass


class StorageQuotaExceededError(StorageError):
    """Raised when an operation would exceed a host-managed storage quota."""

    pass


class StorageTransactionError(StorageError):
    """Raised for invalid transaction lifecycle or nesting."""

    pass


class StorageQueueFullError(StorageError):
    """Raised when the bounded asynchronous storage queue is full."""

    pass


class StorageMigrationError(StorageError):
    """Raised when schema migration validation or commit fails."""

    pass


@dataclass(frozen=True)
class StorageLimits:
    """Immutable snapshot of per-plugin value and private-file quotas."""

    max_entries: int = 4096
    max_value_bytes: int = 256 * 1024
    max_total_value_bytes: int = 4 * 1024 * 1024
    max_files: int = 512
    max_file_bytes: int = 8 * 1024 * 1024
    max_total_file_bytes: int = 32 * 1024 * 1024


@dataclass(frozen=True)
class StorageStatistics:
    """Immutable snapshot of current plugin storage usage and schema version."""

    value_count: int
    value_bytes: int
    schema_version: int
    limits: StorageLimits
    file_count: int = 0
    file_bytes: int = 0


ResultType = TypeVar("ResultType")


@dataclass(frozen=True)
class StorageResult(Generic[ResultType]):
    """Immutable value-or-error result delivered by an asynchronous operation."""

    value: ResultType | None = None
    error: BaseException | None = None

    def unwrap(self) -> ResultType:
        """Return the value, or re-raise the captured operation error."""

        if self.error is not None:
            raise self.error

        return self.value


class StorageTask(Generic[ResultType]):
    """Cancelable handle for one asynchronous storage operation."""

    def __init__(self):
        self._lock = Lock()
        self._started = False
        self._done = False
        self._cancelled = False
        self._result = None

    @property
    def done(self) -> bool:
        """Return whether the task completed, failed, or was cancelled."""

        with self._lock:
            return self._done

    @property
    def cancelled(self) -> bool:
        """Return whether cancellation won before result delivery."""

        with self._lock:
            return self._cancelled

    @property
    def result(self) -> StorageResult[ResultType]:
        """Return the completed result or raise if pending or cancelled."""

        with self._lock:
            if self._cancelled:
                raise RuntimeError("Storage task was cancelled")

            if not self._done:
                raise RuntimeError("Storage task has not completed")

            return self._result

    def cancel(self) -> bool:
        """Cancel pending delivery, returning ``False`` after completion."""

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

    def file_contains(self, scope: Any, path: str) -> bool:
        ...

    def file_read(self, scope: Any, path: str) -> bytes | None:
        ...

    def file_write(self, scope: Any, path: str, data: bytes) -> None:
        ...

    def file_delete(self, scope: Any, path: str) -> bool:
        ...

    def file_clear(self, scope: Any) -> None:
        ...


_MISSING = object()
_DELETED = object()
_KEY_PATTERN = re.compile(r"[a-z][a-z0-9_.-]{0,127}")
_FILE_COMPONENT_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


def _validate_key(key: str) -> str:
    if (
            not isinstance(key, str)
            or _KEY_PATTERN.fullmatch(key) is None
            or len(key.encode("utf-8")) > 128
    ):
        raise ValueError("Invalid storage key")

    return key


def _validate_file_path(path: str) -> str:
    if not isinstance(path, str) or not path or len(path.encode("utf-8")) > 240:
        raise ValueError("Invalid storage file path")

    components = path.split("/")

    if (
            len(components) > 16
            or any(_FILE_COMPONENT_PATTERN.fullmatch(component) is None
                   for component in components)
    ):
        raise ValueError("Invalid storage file path")

    return path


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
    """Typed key-value access to one global or captured account scope.

    Missing keys raise ``KeyError`` unless a default is supplied. Values are
    copied through validated host storage and remain subject to plugin quotas.
    """

    def __init__(self, storage, scope: Any, transaction=None):
        self._storage = storage
        self._scope = scope
        self._transaction = transaction

    def contains(self, key: str) -> bool:
        """Return whether the scope contains ``key``."""

        return self._read(_validate_key(key)) is not None

    def delete(self, key: str) -> bool:
        """Delete ``key`` and return whether it previously existed."""

        key = _validate_key(key)
        existed = self._read(key) is not None
        self._write(key, _DELETED)

        return existed

    def clear(self) -> None:
        """Delete all key-value entries in this scope."""

        if self._transaction is not None:
            self._transaction._clear(self._scope)
            return

        self._storage._require_no_transaction()
        self._storage._apply((_StorageOperation("clear", self._scope),))

    def get_bool(self, key: str, default: Any = _MISSING) -> bool:
        """Read a boolean value, optionally returning ``default`` if absent."""

        return self._get(key, "bool", default)

    def set_bool(self, key: str, value: bool) -> None:
        """Store a boolean value."""

        self._write(_validate_key(key), _stored("bool", value))

    def get_int(self, key: str, default: Any = _MISSING) -> int:
        """Read a signed 64-bit integer, or the optional default."""

        return self._get(key, "int", default)

    def set_int(self, key: str, value: int) -> None:
        """Store a signed 64-bit integer."""

        self._write(_validate_key(key), _stored("int", value))

    def get_float(self, key: str, default: Any = _MISSING) -> float:
        """Read a finite float, or the optional default."""

        return self._get(key, "float", default)

    def set_float(self, key: str, value: float) -> None:
        """Store a finite float."""

        self._write(_validate_key(key), _stored("float", value))

    def get_string(self, key: str, default: Any = _MISSING) -> str:
        """Read a string, or the optional default."""

        return self._get(key, "string", default)

    def set_string(self, key: str, value: str) -> None:
        """Store a UTF-8 string subject to the per-value byte limit."""

        self._write(_validate_key(key), _stored("string", value))

    def get_bytes(self, key: str, default: Any = _MISSING) -> bytes:
        """Read an immutable bytes copy, or the optional default."""

        return self._get(key, "bytes", default)

    def set_bytes(self, key: str, value: bytes) -> None:
        """Store bytes subject to the per-value byte limit."""

        self._write(_validate_key(key), _stored("bytes", value))

    def get_json(self, key: str, default: Any = _MISSING) -> Any:
        """Read a detached JSON-compatible value, or the optional default."""

        value = self._read(_validate_key(key))

        if value is None:
            if default is _MISSING:
                raise KeyError(key)

            if default is None:
                return None

            return _decode(_stored("json", default), "json")

        return _decode(value, "json")

    def set_json(self, key: str, value: Any) -> None:
        """Store a JSON-compatible value with finite numbers and depth at most 32."""

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


class StorageFileScope:
    """Private byte-file access to one global or captured account scope.

    Logical paths never expose physical filesystem locations. File operations
    are not part of key-value transactions or schema migration rollback.
    """

    def __init__(self, storage, scope: Any):
        self._storage = storage
        self._scope = scope

    def contains(self, path: str) -> bool:
        """Return whether a private file exists at the logical path."""

        return self._storage._file_contains(self._scope, _validate_file_path(path))

    def read_bytes(self, path: str) -> bytes:
        """Return a bytes copy, raising ``KeyError`` when the file is absent."""

        path = _validate_file_path(path)
        value = self._storage._file_read(self._scope, path)

        if value is None:
            raise KeyError(path)

        return bytes(value)

    def write_bytes(self, path: str, data: bytes) -> None:
        """Atomically replace a private file subject to storage quotas."""

        path = _validate_file_path(path)

        if not isinstance(data, bytes):
            raise TypeError("Storage file data must be bytes")

        self._storage._file_write(self._scope, path, bytes(data))

    def delete(self, path: str) -> bool:
        """Delete a private file and return whether it existed."""

        return self._storage._file_delete(self._scope, _validate_file_path(path))

    def clear(self) -> None:
        """Delete every private file in this scope."""

        self._storage._file_clear(self._scope)


class StorageTransaction:
    """Single-use atomic key-value transaction across plugin scopes.

    Use as a context manager. Normal exit commits all changes atomically;
    exceptional exit rolls them back. Nested transactions are unsupported.
    """

    def __init__(self, storage):
        self._storage = storage
        self._changes = OrderedChanges()
        self._cleared = set()
        self._entered = False
        self._closed = False
        self._schema_version = None

    @property
    def global_scope(self) -> StorageScope:
        """Return the transaction-bound global scope."""

        self._require_open()
        return StorageScope(self._storage, self._storage._global_scope(), self)

    def account(self, account: int) -> StorageScope:
        """Return a transaction-bound scope for the account's captured owner."""

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
    """Atomic storage migration from one schema version to another.

    The host creates this object before plugin startup. It is single-use and
    commits the target version only when its context exits normally.
    """

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
        """Return the migration's transaction-bound global scope."""

        return self._transaction.global_scope

    def account(self, account: int) -> StorageScope:
        """Return a migration scope for the account's captured owner."""

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
    """Facade for host-managed plugin key-value and private-file storage.

    Direct operations must run off the UI thread. Account scopes capture the
    current owner and fail if that account slot is later reused.
    """

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
        """Return the plugin-wide key-value scope."""

        return StorageScope(self, self._global_scope())

    @property
    def global_files(self) -> StorageFileScope:
        """Return the plugin-wide private-file scope."""

        return StorageFileScope(self, self._global_scope())

    def account(self, account: int) -> StorageScope:
        """Return key-value storage bound to the account's current owner."""

        return StorageScope(self, self._account_scope(account))

    def account_files(self, account: int) -> StorageFileScope:
        """Return private-file storage bound to the account's current owner."""

        return StorageFileScope(self, self._account_scope(account))

    def transaction(self) -> StorageTransaction:
        """Create a single-use atomic key-value transaction."""

        self._require_port()
        return StorageTransaction(self)

    def statistics(self) -> StorageStatistics:
        """Return an immutable usage and limits snapshot."""

        return self._require_port().statistics()

    def clear_all(self) -> None:
        """Delete all plugin key-value entries across every scope."""

        self._require_no_transaction()
        self._apply((_StorageOperation("clear_all", None),))

    def submit(
            self,
            operation,
            *,
            on_complete=None,
    ) -> StorageTask:
        """Queue storage work on the plugin executor.

        At most two storage operations may be outstanding. ``operation`` runs
        off the UI thread; ``on_complete`` receives a ``StorageResult`` on the
        UI thread only while the runtime remains active.
        """

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

    def _file_contains(self, scope: Any, path: str) -> bool:
        return self._require_port().file_contains(scope, path)

    def _file_read(self, scope: Any, path: str) -> bytes | None:
        return self._require_port().file_read(scope, path)

    def _file_write(self, scope: Any, path: str, data: bytes) -> None:
        self._require_port().file_write(scope, path, data)

    def _file_delete(self, scope: Any, path: str) -> bool:
        return self._require_port().file_delete(scope, path)

    def _file_clear(self, scope: Any) -> None:
        self._require_port().file_clear(scope)

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


def _migrate_plugin_storage(plugin_class: Any, storage: Storage) -> None:
    target = plugin_class.storage_schema_version

    if type(target) is not int or target < 0:
        raise StorageMigrationError(
            "storage_schema_version must be a non-negative integer"
        )

    current = storage.statistics().schema_version

    if target < current:
        raise StorageMigrationError(
            f"Storage schema {current} is newer than plugin schema {target}"
        )

    if target == current:
        return

    try:
        with StorageMigration(storage, current, target) as migration:
            plugin_class.migrate_storage(migration)
    except StorageMigrationError:
        raise
    except BaseException as error:
        raise StorageMigrationError(
            f"Storage migration {current} -> {target} failed"
        ) from error
