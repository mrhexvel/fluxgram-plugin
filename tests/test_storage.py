import unittest

from fluxgram import (
    Plugin,
    StorageInactiveError,
    StorageMigrationError,
    StorageOwnerChangedError,
    StorageQueueFullError,
    StorageQuotaExceededError,
    StorageThreadError,
    StorageTransactionError,
    StorageTypeError,
)
from fluxgram.storage import Storage, StorageLimits
from fluxgram.testing import HarnessStorageBackend, PluginHarness


class EmptyPlugin(Plugin):
    pass


class MigratingPlugin(Plugin):
    storage_schema_version = 2
    calls = []
    constructed = 0

    def __init__(self, plugin_id):
        type(self).constructed += 1
        super().__init__(plugin_id)

    @classmethod
    def migrate_storage(cls, migration):
        if cls.constructed != 0:
            raise AssertionError("Migration created a normal plugin runtime")

        cls.calls.append(("migrate", migration.from_version, migration.to_version))

        if migration.from_version < 1:
            migration.global_scope.set_string("mode", "default")

        if migration.from_version < 2:
            current = migration.global_scope.get_int("count", 0)
            migration.global_scope.set_int("count", current + 1)

    def on_load(self):
        type(self).calls.append(("load", self.storage.statistics().schema_version))


class FailingMigrationPlugin(Plugin):
    storage_schema_version = 1

    @classmethod
    def migrate_storage(cls, migration):
        migration.global_scope.set_string("temporary", "value")
        raise RuntimeError("migration failed")


class StorageTest(unittest.TestCase):
    def test_typed_values_and_json_round_trip(self):
        harness = PluginHarness(EmptyPlugin)
        scope = harness.plugin.storage.global_scope
        original = {"enabled": True, "items": [1, None, 2.5]}

        scope.set_bool("enabled", True)
        scope.set_int("count", 7)
        scope.set_float("ratio", 1.5)
        scope.set_string("title", "FluxGram")
        scope.set_bytes("payload", b"data")
        scope.set_json("state", original)
        original["items"].append(9)

        self.assertTrue(scope.get_bool("enabled"))
        self.assertEqual(scope.get_int("count"), 7)
        self.assertEqual(scope.get_float("ratio"), 1.5)
        self.assertEqual(scope.get_string("title"), "FluxGram")
        self.assertEqual(scope.get_bytes("payload"), b"data")
        self.assertEqual(
            scope.get_json("state"),
            {"enabled": True, "items": [1, None, 2.5]},
        )

        scope.set_json("null", None)
        self.assertTrue(scope.contains("null"))
        self.assertIsNone(scope.get_json("null"))
        self.assertEqual(scope.get_json("missing", {"fallback": []}), {"fallback": []})

        with self.assertRaises(KeyError):
            scope.get_string("missing")

        with self.assertRaises(StorageTypeError):
            scope.get_string("count")

        harness.disable()

    def test_validation_rejects_ambiguous_or_unsafe_values(self):
        harness = PluginHarness(EmptyPlugin)
        scope = harness.plugin.storage.global_scope

        with self.assertRaises(TypeError):
            scope.set_int("count", True)

        with self.assertRaises(ValueError):
            scope.set_int("count", 2**63)

        with self.assertRaises(ValueError):
            scope.set_float("ratio", float("nan"))

        with self.assertRaises(ValueError):
            scope.set_json("state", {"ratio": float("inf")})

        with self.assertRaises(ValueError):
            scope.set_string("../escape", "no")

        harness.disable()

    def test_transaction_commits_and_rolls_back_atomically(self):
        harness = PluginHarness(EmptyPlugin)
        storage = harness.plugin.storage

        with storage.transaction() as transaction:
            transaction.global_scope.set_int("count", 1)
            account = transaction.account(0)
            account.set_string("name", "owner-1")
            self.assertEqual(transaction.global_scope.get_int("count"), 1)

            with self.assertRaisesRegex(StorageTransactionError, "active"):
                storage.global_scope.set_int("outside", 3)

        self.assertEqual(storage.global_scope.get_int("count"), 1)
        self.assertEqual(storage.account(0).get_string("name"), "owner-1")

        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with storage.transaction() as transaction:
                transaction.global_scope.set_int("count", 2)
                transaction.account(0).clear()
                raise RuntimeError("rollback")

        self.assertEqual(storage.global_scope.get_int("count"), 1)
        self.assertEqual(storage.account(0).get_string("name"), "owner-1")

        with storage.transaction():
            with self.assertRaisesRegex(StorageTransactionError, "Nested"):
                with storage.transaction():
                    pass

        harness.disable()

    def test_account_scope_captures_owner(self):
        backend = HarnessStorageBackend(owners={0: 100})
        harness = PluginHarness(EmptyPlugin, storage_backend=backend)
        first_owner = harness.plugin.storage.account(0)
        first_owner.set_string("token", "first")
        harness.plugin.storage.account_files(0).write_bytes("owner.bin", b"first")
        backend.set_owner(0, 200)

        with self.assertRaises(StorageOwnerChangedError):
            first_owner.get_string("token")

        second_owner = harness.plugin.storage.account(0)
        self.assertFalse(second_owner.contains("token"))
        second_owner.set_string("token", "second")
        backend.set_owner(0, 100)
        self.assertEqual(harness.plugin.storage.account(0).get_string("token"), "first")

        backend.clear_owner(0, 100)
        self.assertFalse(harness.plugin.storage.account(0).contains("token"))
        self.assertFalse(
            harness.plugin.storage.account_files(0).contains("owner.bin")
        )
        harness.disable()

    def test_owner_change_rolls_back_transaction(self):
        backend = HarnessStorageBackend(owners={0: 100})
        harness = PluginHarness(EmptyPlugin, storage_backend=backend)
        storage = harness.plugin.storage

        with self.assertRaises(StorageOwnerChangedError):
            with storage.transaction() as transaction:
                transaction.global_scope.set_int("global", 1)
                transaction.account(0).set_int("account", 2)
                backend.set_owner(0, 200)

        self.assertFalse(storage.global_scope.contains("global"))
        self.assertFalse(storage.account(0).contains("account"))
        harness.disable()

    def test_namespaces_are_persistent_and_isolated(self):
        backend = HarnessStorageBackend()
        first = PluginHarness(
            EmptyPlugin,
            plugin_id="example.first",
            storage_backend=backend,
        )
        first.plugin.storage.global_scope.set_int("value", 10)
        first.disable()

        restarted = PluginHarness(
            EmptyPlugin,
            plugin_id="example.first",
            storage_backend=backend,
        )
        other = PluginHarness(
            EmptyPlugin,
            plugin_id="example.other",
            storage_backend=backend,
        )

        self.assertEqual(restarted.plugin.storage.global_scope.get_int("value"), 10)
        self.assertFalse(other.plugin.storage.global_scope.contains("value"))
        restarted.disable()
        other.disable()

    def test_private_files_are_persistent_and_owner_scoped(self):
        backend = HarnessStorageBackend(owners={0: 100})
        first = PluginHarness(EmptyPlugin, storage_backend=backend)
        global_files = first.plugin.storage.global_files
        owner_files = first.plugin.storage.account_files(0)

        global_files.write_bytes("cache/avatar.bin", b"global")
        owner_files.write_bytes("state.bin", b"owner-100")
        first.disable()

        restarted = PluginHarness(EmptyPlugin, storage_backend=backend)
        self.assertEqual(
            restarted.plugin.storage.global_files.read_bytes("cache/avatar.bin"),
            b"global",
        )
        self.assertEqual(
            restarted.plugin.storage.account_files(0).read_bytes("state.bin"),
            b"owner-100",
        )

        stale = restarted.plugin.storage.account_files(0)
        backend.set_owner(0, 200)

        with self.assertRaises(StorageOwnerChangedError):
            stale.contains("state.bin")

        self.assertFalse(
            restarted.plugin.storage.account_files(0).contains("state.bin")
        )
        restarted.disable()

    def test_private_file_paths_and_types_are_validated(self):
        harness = PluginHarness(EmptyPlugin)
        files = harness.plugin.storage.global_files

        for path in ("", "/root", "root/", "a//b", ".", "..", "a/../b", "a\\b"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                files.contains(path)

        with self.assertRaises(TypeError):
            files.write_bytes("value.bin", bytearray(b"value"))

        with self.assertRaises(KeyError):
            files.read_bytes("missing.bin")

        harness.disable()

    def test_private_file_quota_replace_and_clear(self):
        backend = HarnessStorageBackend(limits=StorageLimits(
            max_files=2,
            max_file_bytes=5,
            max_total_file_bytes=7,
        ))
        harness = PluginHarness(EmptyPlugin, storage_backend=backend)
        storage = harness.plugin.storage
        global_files = storage.global_files
        account_files = storage.account_files(0)

        global_files.write_bytes("first.bin", b"12345")
        global_files.write_bytes("first.bin", b"12")
        account_files.write_bytes("second.bin", b"12345")

        with self.assertRaises(StorageQuotaExceededError):
            global_files.write_bytes("third.bin", b"x")

        with self.assertRaises(StorageQuotaExceededError):
            global_files.write_bytes("first.bin", b"123456")

        self.assertEqual(global_files.read_bytes("first.bin"), b"12")
        statistics = storage.statistics()
        self.assertEqual((statistics.file_count, statistics.file_bytes), (2, 7))

        account_files.clear()
        self.assertTrue(global_files.contains("first.bin"))
        self.assertFalse(account_files.contains("second.bin"))
        harness.disable()

    def test_clear_scope_and_all_are_independent(self):
        harness = PluginHarness(EmptyPlugin)
        storage = harness.plugin.storage
        storage.global_scope.set_int("global", 1)
        storage.account(0).set_int("account", 2)

        storage.account(0).clear()
        self.assertEqual(storage.global_scope.get_int("global"), 1)
        self.assertFalse(storage.account(0).contains("account"))

        storage.account(0).set_int("account", 2)
        storage.clear_all()
        self.assertEqual(storage.statistics().value_count, 0)
        self.assertEqual(storage.statistics().schema_version, 0)
        harness.disable()

    def test_quota_failure_does_not_partially_commit(self):
        backend = HarnessStorageBackend(limits=StorageLimits(
            max_entries=2,
            max_value_bytes=8,
            max_total_value_bytes=10,
        ))
        harness = PluginHarness(EmptyPlugin, storage_backend=backend)
        storage = harness.plugin.storage

        with self.assertRaises(StorageQuotaExceededError):
            with storage.transaction() as transaction:
                transaction.global_scope.set_string("first", "123456")
                transaction.global_scope.set_string("second", "123456")

        self.assertEqual(storage.statistics().value_count, 0)

        with self.assertRaises(StorageQuotaExceededError):
            storage.global_scope.set_bytes("large", b"123456789")

        self.assertEqual(storage.statistics().value_count, 0)
        harness.disable()

    def test_direct_io_on_ui_thread_is_rejected(self):
        harness = PluginHarness(EmptyPlugin)

        with self.assertRaises(StorageThreadError):
            harness.run_ui(lambda: harness.plugin.storage.global_scope.contains("value"))

        harness.disable()

    def test_submit_moves_storage_io_off_ui_and_completes_on_ui(self):
        harness = PluginHarness(EmptyPlugin)
        completions = []

        task = harness.run_ui(lambda: harness.plugin.storage.submit(
            lambda storage: storage.global_scope.set_int("count", 7) or "stored",
            on_complete=lambda result: completions.append(
                (result.unwrap(), harness._ui_thread.is_ui_thread())
            ),
        ))

        self.assertFalse(task.done)
        harness.drain_storage()
        self.assertTrue(task.done)
        self.assertEqual(task.result.unwrap(), "stored")
        self.assertEqual(completions, [])
        harness.drain_ui()
        self.assertEqual(completions, [("stored", True)])
        self.assertEqual(harness.plugin.storage.global_scope.get_int("count"), 7)
        harness.disable()

    def test_submit_captures_operation_error(self):
        harness = PluginHarness(EmptyPlugin)

        task = harness.plugin.storage.submit(
            lambda storage: storage.global_scope.get_int("missing")
        )
        harness.drain_storage()

        self.assertIsInstance(task.result.error, KeyError)

        with self.assertRaises(KeyError):
            task.result.unwrap()

        harness.disable()

    def test_submit_is_bounded_and_cancelled_on_unload(self):
        harness = PluginHarness(EmptyPlugin)
        first = harness.plugin.storage.submit(lambda storage: 1)
        second = harness.plugin.storage.submit(lambda storage: 2)
        rejected = harness.plugin.storage.submit(lambda storage: 3)

        self.assertTrue(rejected.done)
        self.assertIsInstance(rejected.result.error, StorageQueueFullError)

        harness.disable()

        self.assertTrue(first.cancelled)
        self.assertTrue(second.cancelled)
        self.assertTrue(first.done)
        self.assertTrue(second.done)

    def test_submit_counts_running_task_and_suppresses_stale_completion(self):
        harness = PluginHarness(EmptyPlugin)
        nested = []
        completions = []

        def operation(storage):
            nested.append(storage.submit(lambda current: 2))
            nested.append(storage.submit(lambda current: 3))
            return 1

        task = harness.plugin.storage.submit(
            operation,
            on_complete=lambda result: completions.append(result.unwrap()),
        )
        harness.drain_storage()

        self.assertEqual(task.result.unwrap(), 1)
        self.assertFalse(nested[0].result.error)
        self.assertIsInstance(nested[1].result.error, StorageQueueFullError)

        harness.invalidate_generation()
        harness.drain_ui()
        self.assertEqual(completions, [])

        inactive = harness.plugin.storage.submit(lambda storage: None)
        self.assertIsInstance(inactive.result.error, StorageInactiveError)
        harness.disable()

    def test_storage_close_retries_backend_failure(self):
        class FailingPort:
            def __init__(self):
                self.calls = 0

            def close(self):
                self.calls += 1

                if self.calls == 1:
                    raise RuntimeError("close failed")

        port = FailingPort()
        storage = Storage(port)

        with self.assertRaisesRegex(RuntimeError, "close failed"):
            storage._close()

        with self.assertRaises(StorageInactiveError):
            storage.statistics()

        storage._close()
        storage._close()
        self.assertEqual(port.calls, 2)

    def test_migration_runs_before_load_and_persists_version(self):
        backend = HarnessStorageBackend()
        MigratingPlugin.calls = []
        MigratingPlugin.constructed = 0
        first = PluginHarness(MigratingPlugin, storage_backend=backend)

        self.assertEqual(
            MigratingPlugin.calls,
            [("migrate", 0, 2), ("load", 2)],
        )
        self.assertEqual(first.plugin.storage.global_scope.get_string("mode"), "default")
        self.assertEqual(first.plugin.storage.global_scope.get_int("count"), 1)
        first.disable()

        MigratingPlugin.constructed = 0
        restarted = PluginHarness(MigratingPlugin, storage_backend=backend)
        self.assertEqual(MigratingPlugin.calls[-1], ("load", 2))
        self.assertEqual(MigratingPlugin.calls.count(("migrate", 0, 2)), 1)
        restarted.disable()

    def test_failed_migration_rolls_back_values_and_version(self):
        backend = HarnessStorageBackend()

        with self.assertRaisesRegex(StorageMigrationError, "0 -> 1"):
            PluginHarness(FailingMigrationPlugin, storage_backend=backend)

        harness = PluginHarness(EmptyPlugin, storage_backend=backend)
        self.assertEqual(harness.plugin.storage.statistics().schema_version, 0)
        self.assertFalse(harness.plugin.storage.global_scope.contains("temporary"))
        harness.disable()

    def test_older_plugin_cannot_open_newer_schema(self):
        backend = HarnessStorageBackend()
        MigratingPlugin.constructed = 0
        current = PluginHarness(MigratingPlugin, storage_backend=backend)
        current.disable()

        with self.assertRaisesRegex(StorageMigrationError, "newer"):
            PluginHarness(EmptyPlugin, storage_backend=backend)


if __name__ == "__main__":
    unittest.main()
