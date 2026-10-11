import unittest

from fluxgram import (
    Plugin,
    SelectSetting,
    SliderSetting,
    SwitchSetting,
    TextSetting,
)
from fluxgram.models import ChatInfo, MessageInfo
from fluxgram.testing import (
    PluginHarness,
    before_send,
    chat,
    chat_notifications,
    message,
    message_deleted,
    message_edited,
    message_received,
    notification_override,
)
from fluxgram.ui import Bulletins, Navigation, Notifications


class ExamplePlugin(Plugin):
    settings_schema = (
        SwitchSetting("enabled", "Enabled", default=True),
        SelectSetting(
            "mode",
            "Mode",
            options=(("short", "Short"), ("full", "Full")),
            default="short",
        ),
        SliderSetting("limit", "Limit", minimum=1, maximum=9, default=3, step=2),
        TextSetting("prefix", "Prefix", default="Test", max_length=12),
    )

    def __init__(self, plugin_id):
        super().__init__(plugin_id)
        self.received = []
        self.setting_changes = []
        self.before_send_events = []
        self.unload_count = 0

    def on_load(self):
        self.bulletins = Bulletins(self.ui)
        self.navigation = Navigation(self.ui)
        self.notifications = Notifications(self.ui)
        self.events.subscribe("message.received", self.on_message)
        self.log("loaded")

    def on_message(self, event):
        current = self.client.chats.get(
            account=event.account,
            dialog_id=event.dialog_id,
            topic_id=event.topic_id,
        )
        self.received.append((event, current))
        self.bulletins.show(
            "Message",
            event.text,
            on_click=lambda: self.navigation.open_chat(
                account=event.account,
                dialog_id=event.dialog_id,
                topic_id=event.topic_id,
                message_id=event.message_id,
            ),
        )
        self.notifications.show_message(event)

        return True

    def on_setting_changed(self, key, value):
        self.setting_changes.append((key, value))

    def before_send(self, event):
        self.before_send_events.append(event)

        return event.text_length

    def on_unload(self):
        self.unload_count += 1


class FailingPlugin(Plugin):
    presenter = None
    subscription = None
    unload_count = 0

    def on_load(self):
        bulletins = Bulletins(self.ui)
        type(self).presenter = bulletins._presenter
        type(self).subscription = self.events.subscribe(
            "message.received",
            lambda event: None,
        )

        raise RuntimeError("load failed")

    def on_unload(self):
        type(self).unload_count += 1


class RetryUnloadPlugin(Plugin):
    def __init__(self, plugin_id):
        super().__init__(plugin_id)
        self.unload_count = 0

    def on_unload(self):
        self.unload_count += 1

        if self.unload_count == 1:
            raise RuntimeError("retry unload")


class TestingTest(unittest.TestCase):
    def test_builders_return_public_models(self):
        override = notification_override("until", mute_until=100)
        notifications = chat_notifications(
            chat_override=override,
            effective_muted=True,
        )
        current_chat = chat(
            dialog_id=-10,
            title="Group",
            kind="group",
            notifications=notifications,
        )
        current_message = message(
            message_id=7,
            dialog_id=-10,
            text="Hello",
        )
        event = message_received(
            account=1,
            chat_info=current_chat,
            message_info=current_message,
        )

        self.assertIsInstance(current_chat, ChatInfo)
        self.assertIsInstance(current_message, MessageInfo)
        self.assertEqual(event.dialog_id, -10)
        self.assertEqual(event.text, "Hello")
        self.assertTrue(event.chat.notifications.effective_muted)
        self.assertEqual(message_deleted(8, channel_id=10).dialog_id, -10)
        self.assertEqual(message_edited(edit_date=123).edit_date, 123)
        self.assertTrue(before_send(schedule_date=5).scheduled)

        with self.assertRaisesRegex(ValueError, "dialog IDs"):
            message_received(
                chat_info=chat(dialog_id=1),
                message_info=message(dialog_id=2),
            )

    def test_harness_drives_current_runtime_contract(self):
        current_chat = chat(dialog_id=42, title="Alice")
        event = message_received(
            chat_info=current_chat,
            message_info=message(dialog_id=42, text="Hello"),
        )

        harness = PluginHarness(ExamplePlugin, plugin_id="example.test")
        harness.client.chats.add(current_chat)

        self.assertTrue(harness.active)
        self.assertEqual(harness.settings.get("enabled"), True)
        self.assertEqual(harness.logs, [("example.test", "loaded")])

        harness.settings.set("prefix", "First", dispatch=False)
        harness.settings.set("prefix", "Latest", dispatch=False)
        harness.settings.flush()

        self.assertEqual(
            harness.plugin.setting_changes,
            [("prefix", "Latest")],
        )

        result = harness.emit("message.received", event)

        self.assertEqual(result, (True,))
        self.assertEqual(harness.plugin.received, [(event, current_chat)])
        self.assertEqual(len(harness.ui.notifications), 1)
        bulletin = harness.assert_bulletin_shown("Hello", title="Message")
        harness.run_ui(bulletin.on_click)
        self.assertEqual(harness.ui.navigation[0].dialog_id, 42)

        send_event = before_send(account=0, text_length=12)
        self.assertEqual(harness.dispatch_before_send(send_event), 12)
        self.assertEqual(harness.plugin.before_send_events, [send_event])

        harness.disable()
        harness.disable()
        harness.assert_clean()
        self.assertEqual(harness.plugin.unload_count, 1)

    def test_context_manager_cleans_resources(self):
        with PluginHarness(ExamplePlugin) as harness:
            self.assertEqual(harness.events.active_count, 1)

        harness.assert_clean()
        self.assertTrue(all(
            presenter.dismissed
            for presenter in harness._ui_services.presenters
        ))
        self.assertTrue(all(
            service.closed
            for service in harness._ui_services.notification_services
        ))

    def test_generation_invalidation_suppresses_callbacks(self):
        harness = PluginHarness(ExamplePlugin)
        event = message_received()
        harness.settings.set("enabled", False, dispatch=False)
        harness.invalidate_generation()

        self.assertEqual(harness.emit("message.received", event), ())
        self.assertIsNone(harness.dispatch_before_send(before_send(text_length=1)))
        harness.settings.flush()
        self.assertEqual(harness.plugin.setting_changes, [])

        with self.assertRaisesRegex(RuntimeError, "inactive"):
            harness.run_ui(lambda: harness.client.chats.get(
                account=0,
                dialog_id=1,
            ))

        with self.assertRaisesRegex(RuntimeError, "inactive"):
            harness.plugin.storage.global_scope.contains("value")

        harness.disable()
        harness.assert_clean()

    def test_ui_thread_contract_is_enforced(self):
        harness = PluginHarness(ExamplePlugin)

        with self.assertRaisesRegex(RuntimeError, "UI thread"):
            harness.plugin.navigation.open_chat(account=0, dialog_id=1)

        harness.run_ui(
            lambda: harness.plugin.navigation.open_chat(account=0, dialog_id=1)
        )
        self.assertEqual(len(harness.ui.navigation), 1)
        harness.disable()

    def test_setting_validation_and_raw_fallback(self):
        harness = PluginHarness(ExamplePlugin)

        with self.assertRaises(ValueError):
            harness.settings.set("limit", 4)

        with self.assertRaises(ValueError):
            harness.settings.set("mode", "missing")

        harness.settings.set_raw("limit", "invalid")
        self.assertEqual(harness.settings.get("limit"), 3)
        harness.disable()

    def test_load_failure_cleans_created_resources(self):
        FailingPlugin.presenter = None
        FailingPlugin.subscription = None
        FailingPlugin.unload_count = 0

        with self.assertRaisesRegex(RuntimeError, "load failed"):
            PluginHarness(FailingPlugin)

        self.assertTrue(FailingPlugin.presenter.dismissed)
        self.assertTrue(FailingPlugin.subscription.closed)
        self.assertEqual(FailingPlugin.unload_count, 1)

    def test_failed_unload_can_be_retried(self):
        harness = PluginHarness(RetryUnloadPlugin)

        with self.assertRaisesRegex(RuntimeError, "Plugin cleanup failed"):
            harness.disable()

        harness.disable()
        harness.assert_clean()
        self.assertEqual(harness.plugin.unload_count, 2)

    def test_invalid_plugin_class_is_rejected(self):
        with self.assertRaisesRegex(TypeError, "must inherit"):
            PluginHarness(object)


if __name__ == "__main__":
    unittest.main()
