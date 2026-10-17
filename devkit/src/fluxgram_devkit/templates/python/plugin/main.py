from fluxgram import Plugin, Switch


class PluginEntry(Plugin):

    settings_schema = (
        Switch("enabled", "Enabled", default=True, icon="settings"),
    )

    def on_load(self) -> None:
        self.log("Hello Python Plugin loaded")
