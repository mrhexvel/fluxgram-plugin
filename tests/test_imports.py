from pathlib import Path
import sys
import tomllib
import unittest

import fluxgram
import fluxgram.models
import fluxgram.testing
import fluxgram.ui
from fluxgram import Plugin, StorageResult, StorageTask, SwitchSetting
from fluxgram.storage import Storage, StorageMigration
from fluxgram.testing import PluginHarness
from fluxgram.ui import UI


class ImportsTest(unittest.TestCase):
    def test_distribution_metadata(self):
        metadata = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
        self.assertEqual(metadata["project"]["name"], "fluxgram-plugin")
        self.assertEqual(metadata["project"]["version"], "2.10.0")
        self.assertEqual(metadata["project"]["urls"]["Repository"],
                         "https://github.com/mrhexvel/fluxgram-plugin")

    def test_public_api_imports_without_client_runtime(self):
        self.assertEqual(fluxgram.API_VERSION, 2)
        self.assertEqual(fluxgram.API_MINOR_VERSION, 9)
        self.assertIs(
            fluxgram.ApplicationBackgroundedEvent,
            fluxgram.events.ApplicationBackgroundedEvent,
        )
        self.assertIs(
            fluxgram.ApplicationForegroundedEvent,
            fluxgram.events.ApplicationForegroundedEvent,
        )
        self.assertIs(fluxgram.ChatClosedEvent, fluxgram.events.ChatClosedEvent)
        self.assertIs(fluxgram.ChatOpenedEvent, fluxgram.events.ChatOpenedEvent)
        self.assertIs(fluxgram.MessageDeletedEvent, fluxgram.events.MessageDeletedEvent)
        self.assertIs(fluxgram.MessageEditedEvent, fluxgram.events.MessageEditedEvent)
        self.assertIs(fluxgram.Tasks, fluxgram.tasks.Tasks)
        self.assertIs(fluxgram.Events, fluxgram.events.Events)
        self.assertIs(fluxgram.ChatInfo, fluxgram.models.ChatInfo)
        self.assertEqual(
            set(fluxgram.ui.__all__),
            {"UI", "Bulletins", "Navigation", "Notifications"},
        )
        self.assertFalse(SwitchSetting("enabled", "Enabled").default)
        self.assertFalse(UI()._active())
        self.assertEqual(PluginHarness.__module__, "fluxgram.testing.harness")
        self.assertEqual(Storage.__module__, "fluxgram.storage")
        self.assertEqual(StorageMigration.__module__, "fluxgram.storage")
        self.assertEqual(StorageResult.__module__, "fluxgram.storage")
        self.assertEqual(StorageTask.__module__, "fluxgram.storage")

        with self.assertRaisesRegex(RuntimeError, "only be created by FluxGram"):
            Plugin("standalone.test")

        forbidden = ("android", "java", "org.telegram", "fluxgram_runtime")

        self.assertFalse(any(
            name == prefix or name.startswith(prefix + ".")
            for name in sys.modules
            for prefix in forbidden
        ))


if __name__ == "__main__":
    unittest.main()
