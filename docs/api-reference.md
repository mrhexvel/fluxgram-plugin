# FluxGram Plugin API Reference

FluxGram plugins are trusted code running inside the client process. The APIs below provide lifecycle, compatibility, ownership, and fallback contracts; they are not a sandbox. Python docstrings and Java Javadocs contain symbol-level details and are the source for editor hover.

## Execution model

- Python lifecycle, typed event, storage operation, managed task, and outgoing-transform callbacks run sequentially on the plugin worker unless a specific API says otherwise. `Storage.submit()` completion is delivered on the Android main thread.
- Java module lifecycle runs on the plugin worker. Managed method hooks run synchronously on the hooked method's actual thread.
- Managed Java UI and composer callbacks run on the Android main thread and must not perform I/O, wait, or call Python.
- Disable and unload invalidate the runtime generation before cleanup. Late asynchronous results are ignored.
- Callback budgets diagnose and quarantine slow callbacks; they do not interrupt code which is already running.
- Resources created outside the managed APIs, including direct hooks and plugin-owned threads, remain the plugin's responsibility.

## Python API

### Plugin lifecycle

Subclass `fluxgram.Plugin`. The host creates one instance per runtime generation and calls:

- `migrate_storage()` before instance creation when the declared schema must advance;
- `on_load()` after runtime services are bound;
- `on_setting_changed()` for setting changes without a field-specific callback;
- `transform_outgoing_text()` for supported plain-text composer sends;
- `on_unload()` after managed services begin cleanup.

Cleanup hooks must be idempotent because failed cleanup can be retried. `transform_outgoing_text()` returns a replacement string or `None` to preserve the current pipeline value.

### Commands and transforms

`Plugin.commands` registers exact dot-prefixed command names and aliases. Matching is case-insensitive against the complete first token. Registrations are removed automatically during unload.

`OutgoingTextTransformRequest` is an immutable snapshot containing account owner, dialog/topic identity, current text, notification mode, schedule values, and paid-message stars. The host validates size, timeout, generation, draft identity, and lifecycle before applying a result.

### Typed events

`Plugin.events` exposes typed channels for settings, messages, chat presentation, and application foreground state. Subscriptions are generation-scoped and automatically closed during unload. Events are immutable snapshots, can be dropped when the bounded queue is full, and do not guarantee a matching inverse event.

Legacy string subscriptions remain available for compatibility but have separate execution contracts. Do not subscribe through typed and legacy APIs for the same work unless duplicate processing is intentional.

### Storage

`Plugin.storage` provides global and captured account-owner scopes, typed values, JSON, transactions, private files, statistics, and bounded asynchronous submission.

- Direct storage I/O is forbidden on the UI thread.
- Account scopes reject access after owner reuse.
- KV transactions are atomic; private-file operations are not part of a KV transaction or schema migration.
- `submit()` runs its operation on the plugin executor and delivers `on_complete` on the Android main thread only to the active generation. Completion callbacks must not perform direct storage I/O.
- Namespace and quotas protect host-managed storage consistency but do not isolate full-trust plugin code.

### Managed tasks

`Plugin.tasks` provides immediate, delayed, periodic, debounce, and coalescing operations on the plugin executor. Pending work is cancelled during unload. A cancellation token is cooperative and cannot stop arbitrary Python code which has already started.

### Settings and models

`Switch`, `Select`, `Slider`, and `Text` define declarative settings. Their legacy `*Setting` names are aliases. Icons use the bounded semantic IDs documented in the package README, not Android resource names.

Every setting accepts `visible` and `enabled`. Each value can be a boolean or a callable receiving an immutable mapping of all current, validated setting values. Conditions are evaluated on the plugin worker when the settings screen is described and after a setting changes. A condition must return `bool`; an exception or invalid result leaves the row visible but disabled and is written to the plugin log.

```python
settings_schema = (
    Select(
        "mode",
        "Mode",
        options=(("fixed", "Fixed"), ("custom", "Custom")),
        default="fixed",
    ),
    Slider(
        "duration",
        "Duration",
        minimum=1,
        maximum=60,
        default=5,
        visible=lambda values: values["mode"] == "custom",
    ),
)
```

Chat, message, notification, and event dataclasses are immutable snapshots. A zero `topic_id` means the root dialog; nullable fields represent information the host could not reliably provide.

### UI services

`Plugin.ui` is a lifecycle and access context. Construct independent services with it:

```python
from fluxgram.ui import Bulletins, Navigation, Notifications

bulletins = Bulletins(self.ui)
navigation = Navigation(self.ui)
notifications = Notifications(self.ui)
```

UI services validate main-thread access and register their resources for cleanup. They become permanently inactive when the plugin unloads.

### Testing

`fluxgram.testing.PluginHarness` constructs the same public models and facades on regular CPython without Android. It provides deterministic event delivery, UI calls, storage persistence, owner changes, generation invalidation, fake time, and cleanup assertions.

## Java API 2.0

### Lifecycle and resources

Implement `org.fluxgram.JavaPluginModule`. `onLoad()` receives a generation-scoped `JavaPluginContext`; `onUnload()` must release direct plugin-owned resources and may be retried after failure.

Use `registerResource()` and `registerUiResource()` for host-managed cleanup. Worker resources close in reverse registration order. UI factories and cleanup run on the main thread without blocking the worker.

### Method hooks

`registerMethodHook()` accepts an exact reflected `Method` or `Constructor`. Before and after callbacks receive an isolated argument array and can change arguments, result, or throwable after validation. `invokeOriginal()` is allowed once in the before phase.

Callbacks run synchronously on the target thread. They must not perform I/O, call Python, or wait. Exceptions reject changes from the failing callback, preserve the current multi-hook pipeline outcome, and quarantine its registration. The 4 ms budget is diagnostic, not a timeout. An explicit `invokeOriginal()` executes immediately and its side effects and outcome cannot be rolled back if the generation becomes inactive before callback return.

### Chat selection actions

`registerChatSelectionAction()` contributes one host-owned action for a single server-backed message in an ordinary cloud chat. Availability and invocation run on the main thread. The host revalidates account owner, screen lifecycle, selected message identity, and availability before invocation.

### Composer API

Composer registrations create one effect instance per eligible composer surface. Callbacks run on the main thread and use immutable snapshots plus bounded presentation objects.

- Text offsets are UTF-16 indices bound to a layout revision.
- Geometry is field-local and measured in physical pixels unless a type explicitly states otherwise.
- Captures and draw frames are callback/lifecycle-scoped and must not be retained as general rendering resources.
- Stale revisions, unsafe shaping, invalid presentations, capture failures, and callback failures preserve or restore native drawing according to each interface contract.
- `requestFrame()` is demand-driven and can be coalesced. Continuous animation is bounded by the host.
- Selection decoration is additive; content, cursor, and native-text presentations have their own exclusivity rules.

The build-specific `ComposerInputRendererExtensions` contract is separate from stable Java API 2.0. Its exact host fingerprint is required by schema 5 packages.

## Editor artifacts

`fp prepare-ide` restores generated files under `.fluxgram/ide`:

- the TOML project schema;
- a local Maven module containing the stable Java binary and matching sources JAR;
- fingerprinted host stubs when the project opts into them.

These artifacts are ignored by Git and never included in `.fgplugin`. The devkit builder remains the authoritative Java compiler and packager.
