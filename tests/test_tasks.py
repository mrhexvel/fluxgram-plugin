import unittest

from fluxgram import (
    Plugin,
    TaskCancelledError,
    TaskQueueFullError,
    TaskUnavailableError,
)
from fluxgram._runtime import RuntimeBindings, runtime_scope
from fluxgram.testing import PluginHarness


class TaskPlugin(Plugin):
    pass


class TasksTest(unittest.TestCase):
    def test_submit_runs_on_explicit_drain_and_delivers_result(self):
        harness = PluginHarness(TaskPlugin)
        calls = []
        completions = []
        handle = harness.plugin.tasks.submit(
            lambda token: calls.append(token.cancelled) or 7,
            on_complete=lambda result: completions.append(result.unwrap()),
        )

        self.assertFalse(handle.done)
        self.assertEqual(calls, [])
        harness.tasks.run_ready()

        self.assertEqual(calls, [False])
        self.assertEqual(completions, [7])
        self.assertEqual(handle.result.unwrap(), 7)
        harness.disable()

    def test_delay_uses_fake_clock_and_stable_deadline_order(self):
        harness = PluginHarness(TaskPlugin)
        calls = []
        harness.plugin.tasks.delay(2, lambda token: calls.append("first"))
        harness.plugin.tasks.delay(2, lambda token: calls.append("second"))

        harness.tasks.advance(1.999)
        harness.tasks.run_ready()
        self.assertEqual(calls, [])

        harness.tasks.advance(0.001)
        harness.tasks.run_ready()
        self.assertEqual(calls, ["first", "second"])
        harness.disable()

    def test_cancel_and_unload_drop_pending_callbacks(self):
        harness = PluginHarness(TaskPlugin)
        calls = []
        cancelled = harness.plugin.tasks.delay(
            1,
            lambda token: calls.append("cancelled"),
        )
        unloaded = harness.plugin.tasks.delay(
            1,
            lambda token: calls.append("unloaded"),
        )

        self.assertTrue(cancelled.cancel())
        self.assertFalse(cancelled.cancel())

        with self.assertRaises(TaskCancelledError):
            _ = cancelled.result

        harness.disable()
        harness.tasks.advance(1)
        harness.tasks.run_ready()
        self.assertEqual(calls, [])
        self.assertTrue(unloaded.cancelled)
        harness.assert_clean()

    def test_periodic_is_fixed_delay_and_paused_in_background(self):
        harness = PluginHarness(TaskPlugin)
        calls = []
        handle = harness.plugin.tasks.periodic(
            5,
            lambda token: calls.append(harness.tasks.clock.milliseconds),
            initial_delay=0,
        )

        harness.tasks.run_ready()
        self.assertEqual(calls, [0])
        harness.tasks.advance(20)
        harness.tasks.run_ready()
        self.assertEqual(calls, [0, 20000])

        harness.tasks.set_foreground(False)
        harness.tasks.advance(60)
        harness.tasks.run_ready()
        self.assertEqual(calls, [0, 20000])

        harness.tasks.set_foreground(True)
        harness.tasks.advance(4.999)
        harness.tasks.run_ready()
        self.assertEqual(calls, [0, 20000])
        harness.tasks.advance(0.001)
        harness.tasks.run_ready()
        self.assertEqual(calls, [0, 20000, 85000])
        self.assertTrue(handle.cancel())
        harness.disable()

    def test_periodic_stops_after_callback_error(self):
        harness = PluginHarness(TaskPlugin)
        failure = RuntimeError("periodic failed")
        handle = harness.plugin.tasks.periodic(
            1,
            lambda token: (_ for _ in ()).throw(failure),
            initial_delay=0,
        )

        harness.tasks.run_ready()

        self.assertTrue(handle.done)
        self.assertIs(handle.result.error, failure)
        self.assertEqual(harness.tasks.active_count, 0)
        harness.disable()

    def test_periodic_created_in_background_preserves_initial_delay(self):
        harness = PluginHarness(TaskPlugin)
        calls = []
        harness.tasks.set_foreground(False)
        handle = harness.plugin.tasks.periodic(
            5,
            lambda token: calls.append(harness.tasks.clock.milliseconds),
            initial_delay=0,
        )

        harness.tasks.advance(20)
        harness.tasks.run_ready()
        self.assertEqual(calls, [])

        harness.tasks.set_foreground(True)
        harness.tasks.run_ready()
        self.assertEqual(calls, [20000])

        harness.tasks.advance(5)
        harness.tasks.run_ready()
        self.assertEqual(calls, [20000, 25000])
        self.assertTrue(handle.cancel())
        harness.disable()

    def test_debounce_resets_delay_and_keeps_latest_callback(self):
        harness = PluginHarness(TaskPlugin)
        calls = []
        first = harness.plugin.tasks.debounce(
            "search",
            2,
            lambda token: calls.append("first"),
        )
        harness.tasks.advance(1)
        second = harness.plugin.tasks.debounce(
            "search",
            2,
            lambda token: calls.append("second"),
        )

        self.assertTrue(first.cancelled)
        harness.tasks.advance(1)
        harness.tasks.run_ready()
        self.assertEqual(calls, [])
        harness.tasks.advance(1)
        harness.tasks.run_ready()
        self.assertEqual(calls, ["second"])
        self.assertTrue(second.done)
        harness.disable()

    def test_coalesce_replaces_pending_and_allows_one_trailing_run(self):
        harness = PluginHarness(TaskPlugin)
        calls = []
        first = harness.plugin.tasks.coalesce(
            "refresh",
            lambda token: calls.append("first"),
        )
        replaced = harness.plugin.tasks.coalesce(
            "refresh",
            lambda token: calls.append("latest"),
        )

        self.assertIs(first, replaced)

        def running(token):
            calls.append("running")
            harness.plugin.tasks.coalesce(
                "refresh",
                lambda trailing_token: calls.append("trailing-first"),
            )
            harness.plugin.tasks.coalesce(
                "refresh",
                lambda trailing_token: calls.append("trailing-latest"),
            )

        harness.plugin.tasks.coalesce("running", running)
        harness.tasks.run_ready()

        self.assertEqual(calls, ["latest", "running", "trailing-latest"])
        harness.disable()

    def test_generation_change_suppresses_completion(self):
        harness = PluginHarness(TaskPlugin)
        completions = []

        def operation(token):
            harness.invalidate_generation()
            self.assertTrue(token.cancelled)
            return 5

        handle = harness.plugin.tasks.submit(
            operation,
            on_complete=lambda result: completions.append(result.unwrap()),
        )
        harness.tasks.run_ready()

        self.assertTrue(handle.cancelled)
        self.assertEqual(completions, [])
        harness.disable()

    def test_cancel_after_operation_suppresses_pending_completion(self):
        harness = PluginHarness(TaskPlugin)
        completions = []
        handle = harness.plugin.tasks.submit(
            lambda token: 5,
            on_complete=lambda result: completions.append(result.unwrap()),
        )

        self.assertTrue(harness.tasks.run_next())
        self.assertTrue(handle.done)
        self.assertTrue(handle.cancel())
        harness.tasks.run_ready()

        self.assertEqual(completions, [])
        self.assertTrue(handle.cancelled)
        harness.disable()

    def test_unload_clears_operation_waiting_for_completion(self):
        harness = PluginHarness(TaskPlugin)
        completions = []
        harness.plugin.tasks.submit(
            lambda token: 5,
            on_complete=lambda result: completions.append(result.unwrap()),
        )

        self.assertTrue(harness.tasks.run_next())
        harness.disable()
        harness.tasks.run_ready()

        self.assertEqual(completions, [])
        self.assertEqual(harness.plugin.tasks._records, {})
        self.assertEqual(harness.plugin.tasks._keyed, {})
        harness.assert_clean()

    def test_task_limit_and_argument_validation(self):
        harness = PluginHarness(TaskPlugin)

        for _ in range(harness.plugin.tasks.MAX_TASKS):
            harness.plugin.tasks.delay(60, lambda token: None)

        with self.assertRaises(TaskQueueFullError):
            harness.plugin.tasks.delay(60, lambda token: None)

        with self.assertRaises(ValueError):
            harness.plugin.tasks.periodic(0.5, lambda token: None)

        with self.assertRaises(TypeError):
            harness.plugin.tasks.delay(True, lambda token: None)

        with self.assertRaises(ValueError):
            harness.plugin.tasks.coalesce("", lambda token: None)

        harness.disable()

    def test_runtime_without_tasks_port_fails_closed(self):
        resource = type("Resource", (), {
            "bind_runtime": lambda self, host, generation: None,
            "close": lambda self: None,
            "_bind_runtime": lambda self, host, generation: None,
            "_close": lambda self: None,
        })()
        bindings = RuntimeBindings(
            events=resource,
            client=resource,
            ui_thread=type("UIThread", (), {
                "is_ui_thread": lambda self: False,
                "post": lambda self, callback: callback(),
            })(),
            ui_services=object(),
            call_ui=lambda callback: callback(),
            log=lambda plugin_id, message: None,
        )

        with runtime_scope(lambda plugin_id: bindings):
            plugin = TaskPlugin("test.no.tasks")

        plugin._bind_runtime(type("Host", (), {
            "isGenerationActive": lambda self, generation: True,
        })(), 1)

        with self.assertRaises(TaskUnavailableError):
            plugin.tasks.submit(lambda token: None)

        plugin._unload()


if __name__ == "__main__":
    unittest.main()
