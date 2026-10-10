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
