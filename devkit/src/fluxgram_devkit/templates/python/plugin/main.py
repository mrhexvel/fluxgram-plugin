from fluxgram import Plugin, SwitchSetting


class PluginEntry(Plugin):

    settings_schema = (
        SwitchSetting("enabled", "Enabled", default=True),
    )

    def on_load(self):
        self.log("Hello Python Plugin loaded")
