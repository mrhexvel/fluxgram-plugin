import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

from fluxgram_devkit.cli import DevkitError, api_artifact, initialize_project, inspect_archive, zip_bytes


class DevkitTest(unittest.TestCase):
    @staticmethod
    def dex_fixture(descriptor="Lexample/Module;"):
        encoded = descriptor.encode()
        data = bytearray(152) + bytes([len(encoded)]) + encoded + b"\0"
        data[:8] = b"dex\n035\0"
        struct.pack_into("<III", data, 32, len(data), 112, 0x12345678)
        struct.pack_into("<II", data, 56, 1, 112)
        struct.pack_into("<II", data, 64, 1, 116)
        struct.pack_into("<II", data, 96, 1, 120)
        struct.pack_into("<I", data, 112, 152)
        return bytes(data)

    def manifest(self, **changes):
        value = {
            "schema_version": 2,
            "sdk_version": 2,
            "min_sdk_minor": 9,
            "id": "example.test_plugin",
            "name": "Test Plugin",
            "version": "1.0.0",
            "description": "Test",
            "author": "Tester",
            "entrypoint": "main:PluginEntry",
            "access": "full",
            "declared_capabilities": [],
        }
        value.update(changes)
        return value

    def package(self, manifest, extra=()):
        import json

        files = [("main.py", b"class PluginEntry:\n    pass\n"),
                 ("manifest.json", (json.dumps(manifest) + "\n").encode())]
        files.extend(extra)
        return zip_bytes(files)

    def test_initializes_template(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "plugin"
            initialize_project(target)
            self.assertTrue((target / "fluxgram-plugin.toml").is_file())
            self.assertTrue((target / "plugin/main.py").is_file())
            self.assertTrue((target / "java/src/example/plugin/Module.java").is_file())

    def test_validates_python_package(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "plugin.fgplugin"
            package.write_bytes(self.package(self.manifest()))
            result = inspect_archive(package)
            self.assertEqual(result["id"], "example.test_plugin")
            self.assertEqual(result["sha256"], hashlib.sha256(package.read_bytes()).hexdigest())

    def test_rejects_newer_sdk_and_bundled_sdk(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "plugin.fgplugin"
            package.write_bytes(self.package(self.manifest(min_sdk_minor=10)))

            with self.assertRaises(DevkitError):
                inspect_archive(package)

            package.write_bytes(self.package(self.manifest(), [("fluxgram/__init__.py", b"")]))

            with self.assertRaises(DevkitError):
                inspect_archive(package)

    def test_build_output_is_stable(self):
        files = [("main.py", b"x = 1\n"), ("manifest.json", b"{}\n")]
        self.assertEqual(zip_bytes(files), zip_bytes(reversed(files)))

    def test_rejects_duplicate_json_fields(self):
        data = (b'{"schema_version":2,"schema_version":2,"sdk_version":2,'
                b'"min_sdk_minor":9,"id":"example.test","name":"Test",'
                b'"version":"1.0.0","description":"Test","author":"Test",'
                b'"entrypoint":"main:PluginEntry","access":"full",'
                b'"declared_capabilities":[]}')

        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "plugin.fgplugin"
            package.write_bytes(zip_bytes([("main.py", b""), ("manifest.json", data)]))

            with self.assertRaises(DevkitError):
                inspect_archive(package)

    def test_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "plugin.fgplugin"

            with zipfile.ZipFile(package, "w") as archive:
                info = zipfile.ZipInfo("main.py")
                info.create_system = 3
                info.external_attr = (0o120777 << 16)
                archive.writestr(info, "target")
                archive.writestr("manifest.json", "{}")

            with self.assertRaises(DevkitError):
                inspect_archive(package)

    def test_rejects_case_conflicting_directory_prefixes(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "plugin.fgplugin"
            package.write_bytes(self.package(self.manifest(), [("Assets/a.txt", b"a"),
                                                                 ("assets/b.txt", b"b")]))

            with self.assertRaises(DevkitError):
                inspect_archive(package)

    def test_rejects_invalid_icon_content(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "plugin.fgplugin"
            package.write_bytes(self.package(self.manifest(icon="icon.png"),
                                             [("icon.png", b"not an image payload")]))

            with self.assertRaises(DevkitError):
                inspect_archive(package)

    def test_bundled_java_api_artifact_is_valid(self):
        artifact, metadata = api_artifact()
        self.assertTrue(artifact.is_file())
        self.assertEqual(metadata["api_minor"], 20)

    def test_rejects_dex_that_bundles_host_class(self):
        data = self.dex_fixture("Lorg/telegram/copied/HostClass;")
        java = {
            "path": "java/module.dex",
            "entrypoint": "org.telegram.copied.HostClass",
            "sha256": hashlib.sha256(data).hexdigest(),
            "api_version": 1,
            "min_api_minor": 19,
        }
        manifest = self.manifest(schema_version=3, java_module=java)

        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "plugin.fgplugin"
            package.write_bytes(self.package(manifest, [("java/module.dex", data)]))

            with self.assertRaises(DevkitError):
                inspect_archive(package)


if __name__ == "__main__":
    unittest.main()
