from dataclasses import FrozenInstanceError
import unittest

from fluxgram import (
    ApplicationBackgroundedEvent,
    ApplicationForegroundedEvent,
    ChatClosedEvent,
    ChatOpenedEvent,
    EventContext,
    EventChannel,
    Events,
    ExecutionContext,
    MessageDeletedEvent,
    MessageEditedEvent,
    MessageReceivedEvent,
    Plugin,
    PluginSettingChangedEvent,
    SwitchSetting,
)
from fluxgram.testing import (
    PluginHarness,
    application_backgrounded,
    application_foregrounded,
    chat_closed,
    chat_opened,
    message_deleted,
    message_edited,
    message_received,
    plugin_setting_changed,
)


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


class MessageEditedPlugin(Plugin):
    def __init__(self, plugin_id):
        super().__init__(plugin_id)
        self.calls = []

    def on_load(self):
        self.subscription = self.events.message_edited.subscribe(
            self.calls.append
        )


class MessageDeletedPlugin(Plugin):
    def __init__(self, plugin_id):
        super().__init__(plugin_id)
        self.calls = []

    def on_load(self):
        self.subscription = self.events.message_deleted.subscribe(
            self.calls.append
        )


class ChatLifecyclePlugin(Plugin):
    def __init__(self, plugin_id):
        super().__init__(plugin_id)
        self.calls = []

    def on_load(self):
        self.opened = self.events.chat_opened.subscribe(
            lambda event: self.calls.append(("opened", event))
        )
        self.closed = self.events.chat_closed.subscribe(
            lambda event: self.calls.append(("closed", event))
        )


class ApplicationLifecyclePlugin(Plugin):
    def __init__(self, plugin_id):
        super().__init__(plugin_id)
        self.calls = []

    def on_load(self):
        self.foregrounded = self.events.application_foregrounded.subscribe(
            lambda event: self.calls.append(("foregrounded", event))
        )
        self.backgrounded = self.events.application_backgrounded.subscribe(
            lambda event: self.calls.append(("backgrounded", event))
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

    def test_message_edited_event_is_typed_and_does_not_dedupe_identity(self):
        harness = PluginHarness(MessageEditedPlugin, account=2, owner_id=99)
        first = message_edited(account=2, owner_id=99, edit_date=10)
        second = message_edited(account=2, owner_id=99, edit_date=10)

        self.assertTrue(harness._host.message_edited_active)
        self.assertFalse(harness._host.message_received_active)

        harness.emit_event(first)
        harness.emit_event(second)

        self.assertEqual(harness.plugin.calls, [first, second])
        self.assertEqual(first.context, EventContext(2, 99, 1))

        with self.assertRaises(FrozenInstanceError):
            first.edit_date = 11

        harness.replace_owner(2, 100)
        harness.emit_event(message_edited(account=2, owner_id=99, edit_date=11))
        self.assertEqual(harness.plugin.calls, [first, second])
        harness.disable()
        self.assertFalse(harness._host.message_edited_active)

    def test_message_edited_activation_tracks_its_own_subscriptions(self):
        harness = PluginHarness(EventPlugin)
        received = harness.plugin.events.message_received.subscribe(lambda event: None)
        first = harness.plugin.events.message_edited.subscribe(lambda event: None)
        second = harness.plugin.events.message_edited.subscribe(lambda event: None)

        self.assertTrue(harness._host.message_received_active)
        self.assertTrue(harness._host.message_edited_active)
        first.unsubscribe()
        self.assertTrue(harness._host.message_edited_active)
        second.unsubscribe()
        self.assertFalse(harness._host.message_edited_active)
        self.assertTrue(harness._host.message_received_active)
        received.unsubscribe()
        harness.disable()

    def test_message_edited_requires_valid_snapshot(self):
        event = message_edited()

        with self.assertRaises(ValueError):
            MessageEditedEvent(
                account=1,
                chat=event.chat,
                message=event.message,
                edit_date=1,
                context=EventContext(0, 1, 1),
            )

        with self.assertRaises(ValueError):
            message_edited(edit_date=0)

    def test_message_deleted_event_is_ids_only_and_does_not_dedupe(self):
        harness = PluginHarness(MessageDeletedPlugin, account=2, owner_id=99)
        direct = message_deleted(7, account=2, owner_id=99)
        channel = message_deleted(7, account=2, owner_id=99, channel_id=10)

        self.assertTrue(harness._host.message_deleted_active)
        self.assertFalse(harness._host.message_received_active)
        self.assertFalse(harness._host.message_edited_active)

        harness.emit_event(direct)
        harness.emit_event(direct)
        harness.emit_event(channel)

        self.assertEqual(harness.plugin.calls, [direct, direct, channel])
        self.assertEqual(direct.dialog_id, None)
        self.assertEqual(channel.dialog_id, -10)
        self.assertEqual(channel.account, 2)
        self.assertEqual(channel.owner_id, 99)

        with self.assertRaises(FrozenInstanceError):
            channel.message_id = 8

        harness.replace_owner(2, 100)
        harness.emit_event(message_deleted(8, account=2, owner_id=99))
        self.assertEqual(harness.plugin.calls, [direct, direct, channel])
        harness.disable()
        self.assertFalse(harness._host.message_deleted_active)

    def test_message_deleted_activation_is_independent(self):
        harness = PluginHarness(EventPlugin)
        edited = harness.plugin.events.message_edited.subscribe(lambda event: None)
        first = harness.plugin.events.message_deleted.subscribe(lambda event: None)
        second = harness.plugin.events.message_deleted.subscribe(lambda event: None)

        self.assertTrue(harness._host.message_edited_active)
        self.assertTrue(harness._host.message_deleted_active)
        first.unsubscribe()
        self.assertTrue(harness._host.message_deleted_active)
        second.unsubscribe()
        self.assertFalse(harness._host.message_deleted_active)
        self.assertTrue(harness._host.message_edited_active)
        edited.unsubscribe()
        harness.disable()

    def test_message_deleted_requires_valid_ids(self):
        with self.assertRaises(ValueError):
            message_deleted(0)

        with self.assertRaises(ValueError):
            message_deleted(1, channel_id=0)

        with self.assertRaises(TypeError):
            MessageDeletedEvent(
                context=None,
                message_id=1,
            )

    def test_chat_lifecycle_events_are_typed_immutable_and_owner_scoped(self):
        harness = PluginHarness(ChatLifecyclePlugin, account=2, owner_id=99)
        opened = chat_opened(
            -10,
            presentation_id=50,
            topic_id=7,
            account=2,
            owner_id=99,
        )
        closed = chat_closed(
            -10,
            presentation_id=50,
            topic_id=7,
            account=2,
            owner_id=99,
        )

        self.assertTrue(harness._host.chat_opened_active)
        self.assertTrue(harness._host.chat_closed_active)

        harness.emit_event(opened)
        harness.emit_event(closed)

        self.assertEqual(harness.plugin.calls, [
            ("opened", opened),
            ("closed", closed),
        ])
        self.assertEqual(opened.context, EventContext(2, 99, 1))
        self.assertEqual(opened.presentation_id, closed.presentation_id)

        with self.assertRaises(FrozenInstanceError):
            opened.topic_id = 8

        harness.replace_owner(2, 100)
        harness.emit_event(chat_closed(-10, account=2, owner_id=99))
        self.assertEqual(len(harness.plugin.calls), 2)
        harness.disable()
        self.assertFalse(harness._host.chat_opened_active)
        self.assertFalse(harness._host.chat_closed_active)

    def test_chat_lifecycle_activation_is_independent(self):
        harness = PluginHarness(EventPlugin)
        first = harness.plugin.events.chat_opened.subscribe(lambda event: None)
        second = harness.plugin.events.chat_opened.subscribe(lambda event: None)
        closed = harness.plugin.events.chat_closed.subscribe(lambda event: None)

        self.assertTrue(harness._host.chat_opened_active)
        self.assertTrue(harness._host.chat_closed_active)
        first.unsubscribe()
        self.assertTrue(harness._host.chat_opened_active)
        second.unsubscribe()
        self.assertFalse(harness._host.chat_opened_active)
        self.assertTrue(harness._host.chat_closed_active)
        closed.unsubscribe()
        self.assertFalse(harness._host.chat_closed_active)
        harness.disable()

    def test_chat_lifecycle_requires_valid_identity(self):
        for values in (
                {"dialog_id": 0},
                {"dialog_id": 1, "presentation_id": 0},
                {"dialog_id": 1, "topic_id": -1},
        ):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    chat_opened(**values)

        with self.assertRaises(TypeError):
            ChatClosedEvent(
                context=None,
                presentation_id=1,
                dialog_id=1,
            )

        self.assertIsInstance(chat_opened(), ChatOpenedEvent)
        self.assertIsInstance(chat_closed(), ChatClosedEvent)

    def test_application_lifecycle_events_are_typed_and_owner_scoped(self):
        harness = PluginHarness(ApplicationLifecyclePlugin, account=2, owner_id=99)
        backgrounded = application_backgrounded(account=2, owner_id=99)
        foregrounded = application_foregrounded(account=2, owner_id=99)

        self.assertTrue(harness._host.application_backgrounded_active)
        self.assertTrue(harness._host.application_foregrounded_active)

        harness.emit_event(backgrounded)
        harness.emit_event(foregrounded)

        self.assertEqual(harness.plugin.calls, [
            ("backgrounded", backgrounded),
            ("foregrounded", foregrounded),
        ])
        self.assertEqual(backgrounded.context, EventContext(2, 99, 1))

        with self.assertRaises(FrozenInstanceError):
            foregrounded.context = EventContext(2, 99, 2)

        harness.replace_owner(2, 100)
        harness.emit_event(application_backgrounded(account=2, owner_id=99))
        self.assertEqual(len(harness.plugin.calls), 2)
        harness.disable()
        self.assertFalse(harness._host.application_backgrounded_active)
        self.assertFalse(harness._host.application_foregrounded_active)

    def test_application_lifecycle_activation_is_independent(self):
        harness = PluginHarness(EventPlugin)
        first = harness.plugin.events.application_backgrounded.subscribe(
            lambda event: None
        )
        second = harness.plugin.events.application_backgrounded.subscribe(
            lambda event: None
        )
        foregrounded = harness.plugin.events.application_foregrounded.subscribe(
            lambda event: None
        )

        self.assertTrue(harness._host.application_backgrounded_active)
        self.assertTrue(harness._host.application_foregrounded_active)
        first.unsubscribe()
        self.assertTrue(harness._host.application_backgrounded_active)
        second.unsubscribe()
        self.assertFalse(harness._host.application_backgrounded_active)
        self.assertTrue(harness._host.application_foregrounded_active)
        foregrounded.unsubscribe()
        self.assertFalse(harness._host.application_foregrounded_active)
        harness.disable()

    def test_application_lifecycle_requires_event_context(self):
        with self.assertRaises(TypeError):
            ApplicationForegroundedEvent(context=None)

        with self.assertRaises(TypeError):
            ApplicationBackgroundedEvent(context=None)

        self.assertIsInstance(
            application_foregrounded(),
            ApplicationForegroundedEvent,
        )
        self.assertIsInstance(
            application_backgrounded(),
            ApplicationBackgroundedEvent,
        )

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
