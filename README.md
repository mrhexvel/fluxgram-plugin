# FluxGram SDK

Public Python interfaces, models, and typing information for FluxGram plugins.

The package can be imported on CPython 3.11 or newer for development and static analysis. Runtime services require the Android client and are provided by FluxGram through Chaquopy.

Do not include this package in a `.fgplugin` archive. FluxGram provides the matching SDK at runtime.

## Architecture

The public package contains plugin-facing models, facades, settings, and UI context lifecycle. It does not import Android, Telegram, or `fluxgram_runtime`. The client injects typed runtime bindings while constructing a plugin, and the concrete Telegram adapters remain in the client runtime.

`Plugin` instances are owned by FluxGram and have a single-use lifecycle. Runtime-backed APIs are active after binding and become permanently inactive during unload. UI features are independent services composed with the plugin's `UI` context rather than methods added to the context itself.

```python
from fluxgram import MessageReceivedEvent, Plugin, SwitchSetting
from fluxgram.ui import Notifications


class PluginEntry(Plugin):
    settings_schema = (
        SwitchSetting("enabled", "Enabled", default=True),
    )

    def on_load(self):
        self.notifications = Notifications(self.ui)
        self.events.subscribe("notification.received", self.on_message)

    def on_message(self, event: MessageReceivedEvent):
        if self.settings.get("enabled"):
            return self.notifications.show_message(event)

        return False
```

## Development

Run the standalone checks from the repository root:

```sh
PYTHONPATH=src python3 -B -m unittest discover -s tests
uv build --offline
```

The Android client pins this repository as a submodule and runs additional integration tests against `fluxgram_runtime` and the Java host.

## Testing plugins

`fluxgram.testing` provides a deterministic runtime for testing plugins on regular CPython. It uses the same public models and lifecycle contract as the client without importing Android, Java, Telegram, or `fluxgram_runtime`.

```python
from fluxgram import MessageReceivedEvent, Plugin
from fluxgram.testing import PluginHarness, message_received
from fluxgram.ui import Bulletins


class PluginEntry(Plugin):
    def on_load(self):
        self.bulletins = Bulletins(self.ui)
        self.events.subscribe("message.received", self.on_message)

    def on_message(self, event: MessageReceivedEvent):
        self.bulletins.show("Received", event.text)


def test_message():
    with PluginHarness(PluginEntry) as harness:
        harness.emit("message.received", message_received())
        harness.assert_bulletin_shown("Test message", title="Received")
```

The harness also records navigation and notification calls, provides a fake chat registry, dispatches setting changes, enforces UI-thread access, simulates stale runtime generations, and verifies cleanup after unload.

## Testing storage migrations

The test runtime includes a persistent namespaced storage backend. It supports typed global and account-owner values, JSON, atomic transactions, quotas, schema migrations, rollback, and owner-change checks.

```python
from fluxgram import Plugin
from fluxgram.testing import HarnessStorageBackend, PluginHarness


class PluginEntry(Plugin):
    storage_schema_version = 1

    @classmethod
    def migrate_storage(cls, migration):
        if migration.from_version < 1:
            migration.global_scope.set_int("launch_count", 0)

    def on_load(self):
        with self.storage.transaction() as transaction:
            current = transaction.global_scope.get_int("launch_count")
            transaction.global_scope.set_int("launch_count", current + 1)


backend = HarnessStorageBackend(owners={0: 12345})

with PluginHarness(PluginEntry, storage_backend=backend) as first:
    assert first.plugin.storage.global_scope.get_int("launch_count") == 1

with PluginHarness(PluginEntry, storage_backend=backend) as restarted:
    assert restarted.plugin.storage.global_scope.get_int("launch_count") == 2
```

Direct storage I/O is rejected on the UI thread. Host SDK 2.1 provides the Android KV/JSON backend. UI callbacks can submit bounded storage work and receive the result back on the UI thread:

```python
def on_message(self, event):
    self.storage.submit(
        lambda storage: storage.account(event.account).set_int("last_message", event.message_id),
        on_complete=self.on_stored,
    )

def on_stored(self, result):
    result.unwrap()
```

`submit` returns a `StorageTask`. Queued tasks are cancelled on unload, stale-generation completions are suppressed, and queue saturation is reported as `StorageQueueFullError` in the task result. Plugins which rely on the Android backend must require host SDK 2.1 or newer.

Host SDK 2.2 adds private binary files to the same bounded storage service. Logical paths are validated relative names and never expose an Android filesystem path. Global and captured account-owner scopes are separate:

```python
self.storage.global_files.write_bytes("cache/state.bin", payload)
payload = self.storage.account_files(account).read_bytes("state.bin")
```

File replacement is atomic. File operations are not part of KV transactions or schema migrations. Plugins which use private files must require host SDK 2.2 or newer.
