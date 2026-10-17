from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ..storage import (
    StorageAccountUnavailableError,
    StorageInactiveError,
    StorageLimits,
    StorageOwnerChangedError,
    StorageQuotaExceededError,
    StorageStatistics,
    StorageThreadError,
    _StorageOperation,
    _StoredValue,
)


@dataclass(frozen=True)
class _Scope:
    kind: str
    account: int = -1
    owner: int = 0


@dataclass
class _PluginData:
    values: dict[_Scope, dict[str, _StoredValue]] = field(default_factory=dict)
    files: dict[_Scope, dict[str, bytes]] = field(default_factory=dict)
    schema_version: int = 0


class HarnessStorageBackend:
    """In-memory storage shared by one or more plugin harness instances.

    Reusing a backend preserves plugin namespaces across harness generations and
    allows tests to model account owner changes and cleanup.
    """

    def __init__(
            self,
            *,
            owners: dict[int, int] | None = None,
            limits: StorageLimits | None = None,
    ):
        self._owners = dict(owners or {0: 1})
        self._limits = limits or StorageLimits()
        self._plugins = {}

    def session(
            self,
            plugin_id: str,
            is_ui_thread,
            post_ui=lambda callback: callback(),
    ) -> "HarnessStorageSession":
        """Create a lifecycle session for one plugin storage namespace."""

        data = self._plugins.setdefault(plugin_id, _PluginData())

        return HarnessStorageSession(
            data,
            self._owners,
            self._limits,
            is_ui_thread,
            post_ui,
        )

    def set_owner(self, account: int, owner: int | None) -> None:
        """Set or remove the simulated owner of an account slot."""

        if type(account) is not int or account < 0:
            raise ValueError("account must be a non-negative integer")

        if owner is None:
            self._owners.pop(account, None)
            return

        if type(owner) is not int or owner <= 0:
            raise ValueError("owner must be a positive integer")

        self._owners[account] = owner

    def clear_owner(self, account: int, owner: int) -> None:
        """Delete account-scoped values and files for one exact owner."""

        scope = _Scope("account", account, owner)

        for data in self._plugins.values():
            data.values.pop(scope, None)
            data.files.pop(scope, None)

    def clear_plugin(self, plugin_id: str) -> None:
        """Delete all in-memory host-managed data for a plugin ID."""

        self._plugins.pop(plugin_id, None)


class HarnessStorageSession:
    """Lifecycle-bound in-memory implementation of the production storage port.

    Direct operations reject simulated UI-thread access. Asynchronous submission
    is bounded to two outstanding operations and requires explicit ``drain``.
    """

    def __init__(
            self,
            data: _PluginData,
            owners: dict[int, int],
            limits: StorageLimits,
            is_ui_thread,
            post_ui,
    ):
        self._data = data
        self._owners = owners
        self._limits = limits
        self._is_ui_thread = is_ui_thread
        self._post_ui = post_ui
        self._is_active = lambda: False
        self._pending = deque()
        self._running = 0
        self.closed = False

    def bind_runtime(self, host: Any, generation: int) -> None:
        """Bind the session to an active simulated runtime generation."""

        if self.closed:
            raise StorageInactiveError("Plugin storage is inactive")

        self._is_active = lambda: host.isGenerationActive(generation)

    def close(self) -> None:
        """Close the session and discard queued operations."""

        self.closed = True
        self._is_active = lambda: False
        self._pending.clear()

    def global_scope(self) -> _Scope:
        """Return the internal global scope token for this session."""

        self._require_available()

        return _Scope("global")

    def account_scope(self, account: int) -> _Scope:
        """Capture the current simulated owner of an account scope."""

        self._require_available()
        owner = self._owners.get(account)

        if owner is None or owner <= 0:
            raise StorageAccountUnavailableError(
                f"Telegram account {account} is unavailable"
            )

        return _Scope("account", account, owner)

    def read(self, scope: _Scope, key: str) -> _StoredValue | None:
        """Read a stored value from a validated internal scope."""

        self._require_operation(scope)

        return self._data.values.get(scope, {}).get(key)

    def apply(
            self,
            operations: tuple[_StorageOperation, ...],
            schema_version: int | None = None,
    ) -> None:
        """Atomically apply key-value operations and an optional schema version."""

        self._require_available()
        scopes = {
            operation.scope
            for operation in operations
            if operation.action != "clear_all"
        }

        for scope in scopes:
            self._require_owner(scope)

        values = {
            scope: dict(current)
            for scope, current in self._data.values.items()
        }

        for operation in operations:
            if operation.action == "clear_all":
                values.clear()
                continue

            scope_values = values.setdefault(operation.scope, {})

            if operation.action == "clear":
                scope_values.clear()
            elif operation.action == "delete":
                scope_values.pop(operation.key, None)
            elif operation.action == "set":
                if operation.value.byte_size > self._limits.max_value_bytes:
                    raise StorageQuotaExceededError(
                        "Storage value exceeds the per-value limit"
                    )

                scope_values[operation.key] = operation.value
            else:
                raise ValueError(f"Unsupported storage operation: {operation.action}")

        values = {
            scope: current
            for scope, current in values.items()
            if current
        }
        count = sum(len(current) for current in values.values())
        size = sum(
            value.byte_size
            for current in values.values()
            for value in current.values()
        )

        if count > self._limits.max_entries:
            raise StorageQuotaExceededError("Storage entry limit exceeded")

        if size > self._limits.max_total_value_bytes:
            raise StorageQuotaExceededError("Storage size limit exceeded")

        if schema_version is not None:
            if type(schema_version) is not int or schema_version < 0:
                raise ValueError("Invalid storage schema version")

        for scope in scopes:
            self._require_owner(scope)

        self._data.values = values

        if schema_version is not None:
            self._data.schema_version = schema_version

    def statistics(self) -> StorageStatistics:
        """Return an immutable snapshot of in-memory usage and limits."""

        self._require_available()
        values = self._data.values.values()

        return StorageStatistics(
            value_count=sum(len(current) for current in values),
            value_bytes=sum(
                value.byte_size
                for current in self._data.values.values()
                for value in current.values()
            ),
            schema_version=self._data.schema_version,
            limits=self._limits,
            file_count=sum(len(current) for current in self._data.files.values()),
            file_bytes=sum(
                len(value)
                for current in self._data.files.values()
                for value in current.values()
            ),
        )

    def file_contains(self, scope: _Scope, path: str) -> bool:
        """Return whether a private file exists in an internal scope."""

        self._require_operation(scope)
        return path in self._data.files.get(scope, {})

    def file_read(self, scope: _Scope, path: str) -> bytes | None:
        """Return a private-file bytes copy or ``None`` when absent."""

        self._require_operation(scope)
        value = self._data.files.get(scope, {}).get(path)
        return None if value is None else bytes(value)

    def file_write(self, scope: _Scope, path: str, data: bytes) -> None:
        """Replace a private file after enforcing configured quotas."""

        self._require_operation(scope)

        if len(data) > self._limits.max_file_bytes:
            raise StorageQuotaExceededError(
                "Storage file exceeds the per-file limit"
            )

        files = {
            current_scope: dict(current)
            for current_scope, current in self._data.files.items()
        }
        files.setdefault(scope, {})[path] = bytes(data)
        count = sum(len(current) for current in files.values())
        size = sum(
            len(value)
            for current in files.values()
            for value in current.values()
        )

        if count > self._limits.max_files:
            raise StorageQuotaExceededError("Storage file count limit exceeded")

        if size > self._limits.max_total_file_bytes:
            raise StorageQuotaExceededError("Storage file size limit exceeded")

        self._require_owner(scope)
        self._data.files = files

    def file_delete(self, scope: _Scope, path: str) -> bool:
        """Delete a private file and return whether it existed."""

        self._require_operation(scope)
        current = self._data.files.get(scope)

        if current is None or path not in current:
            return False

        del current[path]

        if not current:
            self._data.files.pop(scope, None)

        return True

    def file_clear(self, scope: _Scope) -> None:
        """Delete all private files in an internal scope."""

        self._require_operation(scope)
        self._data.files.pop(scope, None)

    def submit(self, callback) -> bool | None:
        """Queue work, returning ``False`` when full or ``None`` when inactive."""

        if self.closed or not self._is_active():
            return None

        if self._running + len(self._pending) >= 2:
            return False

        self._pending.append(callback)
        return True

    def complete(self, callback) -> None:
        """Post completion to the configured simulated UI queue."""

        def run():
            if not self.closed and self._is_active():
                callback()

        self._post_ui(run)

    def drain(self) -> None:
        """Run queued storage work until empty outside the simulated UI thread."""

        self._require_active()

        while self._pending:
            callback = self._pending.popleft()
            self._running += 1

            try:
                callback()
            finally:
                self._running -= 1

    def _require_operation(self, scope: _Scope) -> None:
        self._require_available()
        self._require_owner(scope)

    def _require_available(self) -> None:
        self._require_active()

        if self._is_ui_thread():
            raise StorageThreadError(
                "Plugin storage cannot perform direct I/O on the UI thread"
            )

    def _require_active(self) -> None:
        if self.closed or not self._is_active():
            raise StorageInactiveError("Plugin storage is inactive")

    def _require_owner(self, scope: _Scope) -> None:
        if scope.kind == "global":
            return

        if self._owners.get(scope.account) != scope.owner:
            raise StorageOwnerChangedError(
                f"Telegram account {scope.account} changed owner"
            )
