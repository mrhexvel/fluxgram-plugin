import unittest

from fluxgram import (
    CommandInvocation,
    CommandRegistration,
    Commands,
    Plugin,
)
from fluxgram.testing import PluginHarness, outgoing_text_transform


class CommandPlugin(Plugin):
    def __init__(self, plugin_id):
        super().__init__(plugin_id)
        self.invocations = []
        self.registration = None

    def on_load(self):
        self.registration = self.commands.register(
            ".hello",
            self.hello,
            aliases=(".hi",),
        )

    def hello(self, invocation: CommandInvocation) -> str:
        self.invocations.append(invocation)

        return f"Hello, {invocation.arguments}!"


class CommandsTest(unittest.TestCase):
    def test_registered_command_transforms_exact_first_token(self):
        harness = PluginHarness(CommandPlugin)

        self.assertEqual(
            harness.transform_outgoing_text(
                outgoing_text_transform(".hello Alice", dialog_id=-10, topic_id=3)
            ),
            "Hello, Alice!",
        )
        invocation = harness.plugin.invocations[0]
        self.assertEqual(invocation.name, ".hello")
        self.assertEqual(invocation.invoked_as, ".hello")
        self.assertEqual(invocation.arguments, "Alice")
        self.assertEqual(invocation.account, 0)
        self.assertEqual(invocation.dialog_id, -10)
        self.assertEqual(invocation.topic_id, 3)
        self.assertEqual(
            harness.transform_outgoing_text(
                outgoing_text_transform(".helloAlice")
            ),
            ".helloAlice",
        )
        harness.disable()

    def test_aliases_are_case_insensitive_and_preserve_invoked_token(self):
        harness = PluginHarness(CommandPlugin)

        self.assertEqual(
            harness.transform_outgoing_text(
                outgoing_text_transform(".HI    Bob Smith   ")
            ),
            "Hello, Bob Smith!",
        )
        invocation = harness.plugin.invocations[0]
        self.assertEqual(invocation.name, ".hello")
        self.assertEqual(invocation.invoked_as, ".HI")
        self.assertEqual(invocation.arguments, "Bob Smith")
        harness.disable()

    def test_registration_handle_unregisters_and_closes_with_plugin(self):
        harness = PluginHarness(CommandPlugin)
        registration = harness.plugin.registration

        self.assertIsInstance(registration, CommandRegistration)
        self.assertTrue(registration.active)
        self.assertEqual(registration.aliases, (".hi",))
        registration.unregister()
        registration.unregister()
        self.assertFalse(registration.active)
        self.assertEqual(
            harness.transform_outgoing_text(outgoing_text_transform(".hello Alice")),
            ".hello Alice",
        )
        harness.disable()

        self.assertFalse(registration.active)

    def test_decorator_registers_typed_handler(self):
        commands = Commands()

        @commands.command(".ping")
        def ping(invocation: CommandInvocation) -> str:
            return "pong:" + invocation.arguments

        self.assertEqual(commands.active_count, 1)
        result = commands._dispatch(outgoing_text_transform(".ping value"))
        self.assertEqual(result, "pong:value")
        commands._close()

    def test_invalid_and_conflicting_triggers_are_rejected(self):
        commands = Commands()
        handler = lambda invocation: invocation.arguments

        for invalid in ("hello", ".", ".two words", ".привет", "." + "a" * 32):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    commands.register(invalid, handler)

        commands.register(".hello", handler, aliases=(".hi",))

        with self.assertRaisesRegex(ValueError, "already registered"):
            commands.register(".other", handler, aliases=(".HI",))

        with self.assertRaisesRegex(ValueError, "must be unique"):
            commands.register(".same", handler, aliases=(".same",))

        with self.assertRaises(TypeError):
            commands.register(".test", handler, aliases=".alias")

        with self.assertRaises(TypeError):
            commands.register(".test", None)


if __name__ == "__main__":
    unittest.main()
