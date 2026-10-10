import unittest

from fluxgram import Plugin
from fluxgram._runtime import RuntimeBindings, runtime_scope


class Resource:
    def __init__(self):
        self.bindings = []
        self.close_count = 0

    def _bind_runtime(self, host, generation):
        self.bindings.append((host, generation))

    bind_runtime = _bind_runtime

    def _close(self):
        self.close_count += 1

    close = _close


class UIThread:
    def is_ui_thread(self):
        return True

    def post(self, callback):
        callback()


class UIServices:
    pass


class RuntimeContractTest(unittest.TestCase):
    def create_plugin(self):
        self.events = Resource()
        self.client = Resource()
        self.logs = []
        bindings = RuntimeBindings(
            events=self.events,
            client=self.client,
            ui_thread=UIThread(),
            ui_services=UIServices(),
            call_ui=lambda callback: callback(),
            log=lambda plugin_id, message: self.logs.append((plugin_id, message)),
        )

        with runtime_scope(lambda plugin_id: bindings):
            return Plugin("test.runtime")

    def test_runtime_scope_does_not_leak(self):
        plugin = self.create_plugin()
        self.assertEqual(plugin.id, "test.runtime")

        with self.assertRaisesRegex(RuntimeError, "only be created by FluxGram"):
            Plugin("test.outside")

    def test_runtime_is_single_use(self):
        plugin = self.create_plugin()
        host = object()
        plugin._bind_runtime(host, 7)

        self.assertEqual(self.client.bindings, [(host, 7)])
        self.assertEqual(self.events.bindings, [(host, 7)])

        with self.assertRaisesRegex(RuntimeError, "already bound"):
            plugin._bind_runtime(host, 8)

        plugin._unload()
        plugin._unload()
        self.assertEqual(self.client.close_count, 1)
        self.assertEqual(self.events.close_count, 1)

        with self.assertRaisesRegex(RuntimeError, "already bound"):
            plugin._bind_runtime(host, 9)

    def test_log_uses_runtime_port(self):
        plugin = self.create_plugin()
        plugin.log("loaded")
        self.assertEqual(self.logs, [("test.runtime", "loaded")])


if __name__ == "__main__":
    unittest.main()
