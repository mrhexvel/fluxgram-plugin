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
    schema_version: int = 0


class HarnessStorageBackend:
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
        data = self._plugins.setdefault(plugin_id, _PluginData())

        return HarnessStorageSession(
            data,
            self._owners,
            self._limits,
            is_ui_thread,
            post_ui,
        )

    def set_owner(self, account: int, owner: int | None) -> None:
        if type(account) is not int or account < 0:
            raise ValueError("account must be a non-negative integer")

        if owner is None:
            self._owners.pop(account, None)
            return

        if type(owner) is not int or owner <= 0:
            raise ValueError("owner must be a positive integer")

        self._owners[account] = owner

    def clear_owner(self, account: int, owner: int) -> None:
        scope = _Scope("account", account, owner)

        for data in self._plugins.values():
            data.values.pop(scope, None)

    def clear_plugin(self, plugin_id: str) -> None:
        self._plugins.pop(plugin_id, None)


class HarnessStorageSession:
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
        if self.closed:
            raise StorageInactiveError("Plugin storage is inactive")

        self._is_active = lambda: host.isGenerationActive(generation)

    def close(self) -> None:
        self.closed = True
        self._is_active = lambda: False
        self._pending.clear()

    def global_scope(self) -> _Scope:
        self._require_available()

        return _Scope("global")

    def account_scope(self, account: int) -> _Scope:
        self._require_available()
        owner = self._owners.get(account)

        if owner is None or owner <= 0:
            raise StorageAccountUnavailableError(
                f"Telegram account {account} is unavailable"
            )

        return _Scope("account", account, owner)

    def read(self, scope: _Scope, key: str) -> _StoredValue | None:
        self._require_operation(scope)

        return self._data.values.get(scope, {}).get(key)

    def apply(
            self,
            operations: tuple[_StorageOperation, ...],
            schema_version: int | None = None,
    ) -> None:
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
        )

    def submit(self, callback) -> bool | None:
        if self.closed or not self._is_active():
            return None

        if self._running + len(self._pending) >= 2:
            return False

        self._pending.append(callback)
        return True

    def complete(self, callback) -> None:
        def run():
            if not self.closed and self._is_active():
                callback()

        self._post_ui(run)

    def drain(self) -> None:
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
