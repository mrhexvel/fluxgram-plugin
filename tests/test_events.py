from dataclasses import FrozenInstanceError
import unittest

from fluxgram import (
    EventContext,
    EventChannel,
    Events,
    ExecutionContext,
    MessageReceivedEvent,
    Plugin,
    PluginSettingChangedEvent,
    SwitchSetting,
)
from fluxgram.testing import PluginHarness, message_received, plugin_setting_changed


class EventPlugin(Plugin):
    def __init__(self, plugin_id):
        super().__init__(plugin_id)
        self.calls = []
        self.legacy = []

    def on_load(self):
        self.subscription = self.events.plugin_setting_changed.subscribe(
            lambda event: self.calls.append(event)
        )

    def on_setting_changed(self, key, value):
        self.legacy.append((key, value))


class SettingPlugin(EventPlugin):
    settings_schema = (SwitchSetting("enabled", "Enabled"),)

    def on_load(self):
        self.order = []
        self.events.plugin_setting_changed.subscribe(
            lambda event: self.order.append(("typed", event))
        )

    def on_setting_changed(self, key, value):
        self.order.append(("legacy", key, value))


class MessagePlugin(Plugin):
    def __init__(self, plugin_id):
        super().__init__(plugin_id)
        self.calls = []

    def on_load(self):
        self.subscription = self.events.message_received.subscribe(
            self.calls.append
        )


class EventsTest(unittest.TestCase):
    def test_setting_event_is_typed_and_immutable(self):
        harness = PluginHarness(EventPlugin, account=2, owner_id=99)
        order = []
        harness.plugin.subscription.unsubscribe()
        harness.plugin.events.plugin_setting_changed.subscribe(
            lambda event: order.append(("typed", event))
        )
        event = plugin_setting_changed(
            "enabled",
            True,
            context=EventContext(account=2, owner_id=99, generation=1),
        )
        harness.emit_event(event)

        self.assertEqual(order, [("typed", event)])
        self.assertEqual(event.account, 2)
        self.assertEqual(event.owner_id, 99)
        self.assertEqual(event.generation, 1)

        with self.assertRaises(FrozenInstanceError):
            event.key = "other"

        harness.disable()

    def test_harness_setting_dispatches_typed_then_legacy(self):
        harness = PluginHarness(SettingPlugin, account=2, owner_id=99)
        harness.settings.set("enabled", True)

        typed = harness.plugin.order[0][1]
        self.assertEqual(harness.plugin.order, [
            ("typed", typed),
            ("legacy", "enabled", True),
        ])
        self.assertEqual(
            typed,
            PluginSettingChangedEvent(
                EventContext(account=2, owner_id=99, generation=1),
                "enabled",
                True,
            ),
        )
        harness.disable()

    def test_harness_captures_context_and_suppresses_replaced_owner(self):
        harness = PluginHarness(SettingPlugin, account=2, owner_id=99)
        harness.settings.set("enabled", True, dispatch=False)
        harness.set_active_account(3, 100)
        harness.settings.flush()

        event = harness.plugin.order[0][1]
        self.assertEqual(event.context, EventContext(2, 99, 1))

        harness.plugin.order.clear()
        harness.settings.set("enabled", False, dispatch=False)
        harness.replace_owner(3, 101)
        harness.settings.flush()
        self.assertEqual(harness.plugin.order, [])
        harness.disable()

    def test_errors_are_isolated_and_reported(self):
        harness = PluginHarness(EventPlugin)
        calls = []
        harness.plugin.events.plugin_setting_changed.subscribe(
            lambda event: (_ for _ in ()).throw(ValueError("broken"))
        )
        harness.plugin.events.plugin_setting_changed.subscribe(
            lambda event: calls.append(event)
        )
        event = plugin_setting_changed("mode", "compact")

        harness.emit_event(event)

        self.assertEqual(calls, [event])
        self.assertTrue(any("ValueError: broken" in message for _, message in harness.logs))
        harness.disable()

    def test_message_event_is_typed_immutable_and_owner_scoped(self):
        harness = PluginHarness(MessagePlugin, account=2, owner_id=99)
        event = message_received(account=2, owner_id=99)

        self.assertTrue(harness._host.message_received_active)

        harness.emit_event(event)

        self.assertEqual(harness.plugin.calls, [event])
        self.assertEqual(event.context, EventContext(2, 99, 1))

        with self.assertRaises(FrozenInstanceError):
            event.account = 3

        harness.replace_owner(2, 100)
        harness.emit_event(message_received(account=2, owner_id=99))
        self.assertEqual(harness.plugin.calls, [event])
        harness.disable()
        self.assertFalse(harness._host.message_received_active)

    def test_message_delivery_activation_tracks_last_subscription(self):
        harness = PluginHarness(EventPlugin)
        first = harness.plugin.events.message_received.subscribe(lambda event: None)
        second = harness.plugin.events.message_received.subscribe(lambda event: None)

        self.assertTrue(harness._host.message_received_active)
        first.unsubscribe()
        self.assertTrue(harness._host.message_received_active)
        second.unsubscribe()
        self.assertFalse(harness._host.message_received_active)
        harness.disable()

    def test_typed_message_requires_matching_context(self):
        event = message_received()

        with self.assertRaises(ValueError):
            MessageReceivedEvent(
                account=1,
                chat=event.chat,
                message=event.message,
                context=EventContext(0, 1, 1),
            )

        raw_event = MessageReceivedEvent(
            account=0,
            chat=event.chat,
            message=event.message,
        )
        harness = PluginHarness(MessagePlugin)

        with self.assertRaisesRegex(TypeError, "require an EventContext"):
            harness.emit_event(raw_event)

        harness.disable()

    def test_generation_context_and_subscription_lifecycle(self):
        harness = PluginHarness(EventPlugin)
        calls = []
        subscription = harness.plugin.events.plugin_setting_changed.subscribe(calls.append)

        harness.emit_event(plugin_setting_changed(
            "mode",
            "stale",
            context=EventContext(account=0, owner_id=1, generation=2),
        ))
        self.assertEqual(calls, [])

        subscription.unsubscribe()
        harness.emit_event(plugin_setting_changed("mode", "closed"))
        self.assertEqual(calls, [])

        harness.disable()
        self.assertTrue(subscription.closed)
        harness.assert_clean()

    def test_only_plugin_execution_context_is_supported(self):
        harness = PluginHarness(EventPlugin)

        with self.assertRaises(TypeError):
            harness.plugin.events.plugin_setting_changed.subscribe(None)

        with self.assertRaises(ValueError):
            harness.plugin.events.plugin_setting_changed.subscribe(
                lambda event: None,
                context="ui",
            )

        subscription = harness.plugin.events.plugin_setting_changed.subscribe(
            lambda event: None,
            context=ExecutionContext.PLUGIN,
        )
        self.assertFalse(subscription.closed)
        harness.disable()

    def test_invalid_snapshot_and_unregistered_channel_are_rejected(self):
        with self.assertRaises(ValueError):
            EventContext(account=-1, owner_id=1, generation=1)

        with self.assertRaises(ValueError):
            EventContext(account=0, owner_id=1, generation=0)

        with self.assertRaises(TypeError):
            PluginSettingChangedEvent(EventContext(0, 1, 1), "key", [])

        harness = PluginHarness(EventPlugin)
        channel = EventChannel(
            harness.plugin.events,
            "unregistered",
            PluginSettingChangedEvent,
        )

        with self.assertRaises(ValueError):
            channel.subscribe(lambda event: None)

        harness.disable()

    def test_legacy_cleanup_is_retried_after_failure(self):
        class LegacyPort:
            def __init__(self):
                self.close_count = 0

            def bind_runtime(self, host, generation):
                pass

            def subscribe(self, name, callback, *, thread="ui"):
                raise AssertionError("not used")

            def close(self):
                self.close_count += 1

                if self.close_count == 1:
                    raise RuntimeError("cleanup failed")

        port = LegacyPort()
        events = Events(port)
        events.bind_runtime(type("Host", (), {
            "isGenerationActive": lambda self, generation: True,
        })(), 1)
        subscription = events.plugin_setting_changed.subscribe(lambda event: None)

        with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
            events.close()

        self.assertTrue(subscription.closed)
        events.close()
        self.assertEqual(port.close_count, 2)


if __name__ == "__main__":
    unittest.main()
