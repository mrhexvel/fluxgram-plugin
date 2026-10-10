import unittest

from fluxgram import (
    Plugin,
    StorageMigrationError,
    StorageOwnerChangedError,
    StorageQuotaExceededError,
    StorageThreadError,
    StorageTransactionError,
    StorageTypeError,
)
from fluxgram.storage import StorageLimits
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
