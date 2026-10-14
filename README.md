# FluxGram Plugin SDK

Public Python interfaces, models, and typing information for FluxGram plugins.

Install the development package with `pip install fluxgram-plugin`. Python imports remain under `fluxgram`.

The distribution was previously published as `fluxgram-sdk`. Do not install both names in one environment because both distributions provide the same `fluxgram` package. Migrate explicitly:

```sh
python3 -m pip uninstall fluxgram-sdk
python3 -m pip install fluxgram-plugin
```

The package can be imported on CPython 3.11 or newer for development and static analysis. Runtime services require the Android client and are provided by FluxGram through Chaquopy.

Do not include this package in a `.fgplugin` archive. FluxGram provides the matching SDK at runtime.

Plugins are trusted in-process code. FluxGram does not sandbox Python plugins, and a plugin can bypass SDK facades through Java and other process APIs. Lifecycle checks, quotas, immutable models, and host-managed services are compatibility and reliability contracts, not security restrictions. Install and run only code whose source and publisher you trust.

Installing a plugin imports its entrypoint for migration checks even when regular runtime enable is off. Trust a package before installation.

## Architecture

The public package contains plugin-facing models, facades, settings, and UI context lifecycle. It does not import Android, Telegram, or `fluxgram_runtime`. The client injects typed runtime bindings while constructing a plugin, and the concrete Telegram adapters remain in the client runtime.

`Plugin` instances are owned by FluxGram and have a single-use lifecycle. Runtime-backed APIs are active after binding and become permanently inactive during unload. UI features are independent services composed with the plugin's `UI` context rather than methods added to the context itself.

Typed observational events run sequentially on the plugin executor. They carry immutable account, owner, and runtime-generation context, use the host's bounded event queue, and are automatically unsubscribed during unload.

`message_edited` reports the new server-confirmed snapshot of an ordinary cloud message. It includes incoming and outgoing edits, but not scheduled, quick-reply, ephemeral, secret, or service messages. It does not provide the previous version or a text diff, and duplicate delivery remains possible after Telegram update replay.

`message_deleted` reports IDs from accepted ordinary cloud delete updates. Non-channel updates do not identify the dialog, while channel updates provide `channel_id` and `dialog_id`. Deleted message snapshots, topic, sender, direction, actor, reason, and revoke semantics are unavailable. Scheduled, quick-reply, ephemeral, secret, local-only, and history-clearing operations are excluded. Delivery for a deletion initiated on the same device is not guaranteed, and update replay may produce duplicates.

`chat_opened` and `chat_closed` report navigation presentations of ordinary cloud chats after they become fully visible or hidden. Both carry a stable `presentation_id`, `dialog_id`, and `topic_id`. Background and foreground transitions, view recreation, previews, bubbles, embedded chats, secret chats, comments, and special chat modes do not create these events. A forum topic switch closes the old identity and opens the new identity with the same presentation ID. Subscribing does not replay an already open chat, and bounded delivery or subscription changes can produce an event without its pair.

`application_foregrounded` and `application_backgrounded` report stabilized process-wide visibility transitions across all FluxGram activities. A background edge is confirmed after 200 milliseconds to ignore short activity handoffs. Each event captures the selected account at that edge. Account switches, passcode overlays, screen state alone, and ordinary UI navigation are not transitions. Subscribing does not replay the current state, and Android does not guarantee a final background callback before process death.

```python
class PluginEntry(Plugin):
    def on_load(self):
        self.events.message_received.subscribe(self.on_message_received)

    def on_message_received(self, event: MessageReceivedEvent):
        self.log(f"Received {event.message_id} in {event.dialog_id}")
```

The string-based `message.received` subscription is a legacy compatibility API and runs on the UI thread. Do not subscribe through both APIs for the same work. `notification.received` is a separate legacy synchronous interception contract and is not replaced by the observational channel.

```python
from fluxgram import MessageReceivedEvent, Plugin, Switch
from fluxgram.ui import Notifications


class PluginEntry(Plugin):
    settings_schema = (
        Switch("enabled", "Enabled", default=True, icon="notifications"),
    )

    def on_load(self):
        self.notifications = Notifications(self.ui)
        self.events.subscribe("notification.received", self.on_message)

    def on_message(self, event: MessageReceivedEvent):
        if self.settings.get("enabled"):
            return self.notifications.show_message(event)

        return False
```

Settings support the optional semantic icons `adjustments`, `auto_delete`, `blur`,
`clear`, `colors`, `copy`, `curve`, `customize`, `delete`, `edit`, `emoji`,
`favorite`, `file`, `flip`, `link`, `list`, `message`, `notifications`, `open`,
`particles`, `reset`, `retry`, `select`, `send`, `settings`, `speed`, `translate`,
and `zoom_in`. The legacy `SwitchSetting`, `SelectSetting`, `SliderSetting`, and
`TextSetting` names remain aliases for existing plugins.

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

The test runtime includes a persistent namespaced storage backend. It supports typed global and account-owner values, JSON, atomic transactions, quotas, schema migrations, rollback, and owner-change checks. Namespaces and quotas define correct use of the managed storage facade; they do not isolate a full-trust plugin from the application process.

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

Host SDK 2.2 adds private binary files to the same bounded storage service. Logical paths are validated relative names and the facade does not expose an Android filesystem path. Global and captured account-owner scopes are separate:

```python
self.storage.global_files.write_bytes("cache/state.bin", payload)
payload = self.storage.account_files(account).read_bytes("state.bin")
```

File replacement is atomic. File operations are not part of KV transactions or schema migrations. Plugins which use private files must require host SDK 2.2 or newer.

## Managed tasks

Host SDK 2.3 provides lifecycle-aware work on the plugin executor. Every callback receives a cancellation token. Pending work is cancelled during disable or unload, and a completion is delivered only while the same runtime generation remains active:

```python
task = self.tasks.submit(
    lambda token: build_snapshot(token),
    on_complete=lambda result: self.log(result.unwrap()),
)

self.tasks.delay(2.0, lambda token: refresh_once())
self.tasks.periodic(60.0, lambda token: refresh_while_foreground())
self.tasks.debounce("search", 0.4, lambda token: run_latest_search())
self.tasks.coalesce("refresh", lambda token: refresh_latest_state())
```

Periodic tasks use fixed delay, pause while the application is in the background, and do not catch up missed intervals. Cancellation cannot stop Python code which has already started, but it suppresses its completion and future periodic runs. Plugins which use managed tasks must require host SDK 2.3 or newer.

## Typed events

Host SDK 2.4 introduces immutable typed channels which run callbacks on the bounded plugin executor. Subscriptions are automatically removed during unload:

```python
from fluxgram import ExecutionContext

self.events.plugin_setting_changed.subscribe(
    lambda event: self.log(f"{event.key}={event.value}"),
    context=ExecutionContext.PLUGIN,
)
```

`PluginSettingChangedEvent` includes the account, owner and runtime generation captured when the setting changed. Callback return values are ignored, and one failing subscriber does not prevent delivery to the others. Plugins which use typed events must require host SDK 2.4 or newer.

Later channels require the minor which introduced their production host path: `message_received` requires 2.5, `message_edited` 2.6, `message_deleted` 2.7, `chat_opened` and `chat_closed` 2.8, and application lifecycle events 2.9.
