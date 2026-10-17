import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch
import zipfile

from fluxgram_devkit.cli import (
    DevkitError,
    api_artifact,
    api_sources_artifact,
    build_project,
    compile_java,
    host_stubs_artifact,
    initialize_project,
    inspect_archive,
    load_project,
    parser,
    prepare_ide,
    project_schema_artifact,
    zip_bytes,
)


class DevkitTest(unittest.TestCase):
    def test_console_scripts_share_entrypoint(self):
        project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
        scripts = project["project"]["scripts"]

        self.assertEqual(scripts["fp"], "fluxgram_devkit.cli:main")
        self.assertEqual(scripts["fluxgram-plugin"], scripts["fp"])

        with patch.object(sys, "argv", ["fp"]):
            self.assertEqual(parser().prog, "fp")

        with patch.object(sys, "argv", ["fluxgram-plugin"]):
            self.assertEqual(parser().prog, "fluxgram-plugin")

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
            "min_sdk_minor": 10,
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

    def test_initializes_templates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            for template, sections, schema in (
                    ("python", {"python"}, 4),
                    ("java", {"java"}, 5),
                    ("hybrid", {"python", "java"}, 4)):
                target = root / template
                initialize_project(target, template)
                _, config, manifest = load_project(target)
                self.assertEqual(set(config) & {"python", "java"}, sections)
                self.assertEqual(manifest["schema_version"], schema)
                self.assertTrue((target / "README.md").is_file())
                self.assertTrue((target / ".gitignore").is_file())
                self.assertFalse((target / "gitignore").exists())
                self.assertTrue((target / ".fluxgram/ide/fluxgram-plugin.schema.json").is_file())

                if "python" in sections:
                    self.assertTrue((target / "pyrightconfig.json").is_file())
                    requirements = (target / "requirements-dev.txt").read_text()
                    self.assertIn("fluxgram-plugin==2.12.0", requirements)

                requirements = (target / "requirements-dev.txt").read_text()
                self.assertIn("fluxgram-plugin-devkit==2.1.1", requirements)

                if "java" in sections:
                    self.assertTrue((target / "build.gradle").is_file())
                    self.assertTrue((target / "settings.gradle").is_file())
                    module = (target / ".fluxgram/ide/repository/org/fluxgram/"
                              "fluxgram-java-api/2.0")
                    self.assertTrue((module / "fluxgram-java-api-2.0.jar").is_file())
                    self.assertTrue((module / "fluxgram-java-api-2.0-sources.jar").is_file())
                    self.assertTrue((module / "fluxgram-java-api-2.0.pom").is_file())

                self.assertEqual(
                    (target / ".fluxgram/ide/fluxgram-host-stubs.jar").is_file(),
                    template == "java",
                )

            default = root / "default"
            initialize_project(default)
            _, config, _ = load_project(default)
            self.assertIn("python", config)
            self.assertIn("java", config)

    def test_init_and_new_default_to_hybrid(self):
        for command in ("init", "new"):
            arguments = parser().parse_args([command, "plugin"])
            self.assertEqual(arguments.template, "hybrid")

        arguments = parser().parse_args(["new", "plugin", "--template", "java"])
        self.assertEqual(arguments.template, "java")

        arguments = parser().parse_args(["prepare-ide"])
        self.assertEqual(arguments.project, ".")

    def test_prepare_ide_refreshes_only_required_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "plugin"
            initialize_project(project, "java")
            destination = project / ".fluxgram/ide"
            api, _ = api_artifact()
            sources = api_sources_artifact()
            stubs, _ = host_stubs_artifact()
            schema = project_schema_artifact()
            module = destination / "repository/org/fluxgram/fluxgram-java-api/2.0"
            (module / "fluxgram-java-api-2.0.jar").write_bytes(b"stale")
            (module / "fluxgram-java-api-2.0-sources.jar").write_bytes(b"stale")
            prepare_ide(project)
            self.assertEqual((module / "fluxgram-java-api-2.0.jar").read_bytes(), api.read_bytes())
            self.assertEqual((module / "fluxgram-java-api-2.0-sources.jar").read_bytes(),
                             sources.read_bytes())
            self.assertIn("<artifactId>fluxgram-java-api</artifactId>",
                          (module / "fluxgram-java-api-2.0.pom").read_text())
            self.assertEqual((destination / "fluxgram-host-stubs.jar").read_bytes(), stubs.read_bytes())
            self.assertEqual((destination / "fluxgram-plugin.schema.json").read_bytes(),
                             schema.read_bytes())

            config = project / "fluxgram-plugin.toml"
            config.write_text(config.read_text().replace("host_stubs = true", "host_stubs = false"))
            prepare_ide(project)
            self.assertFalse((destination / "fluxgram-host-stubs.jar").exists())

    def test_validates_python_package(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "plugin.fgplugin"
            package.write_bytes(self.package(self.manifest()))
            result = inspect_archive(package)
            self.assertEqual(result["id"], "example.test_plugin")
            self.assertEqual(result["sha256"], hashlib.sha256(package.read_bytes()).hexdigest())

    def test_validates_schema_four_module_compositions(self):
        data = self.dex_fixture()
        java = {
            "path": "java/module.dex",
            "entrypoint": "example.Module",
            "sha256": hashlib.sha256(data).hexdigest(),
            "api_version": 2,
            "min_api_minor": 0,
        }

        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "plugin.fgplugin"
            hybrid = self.manifest(schema_version=4, java_module=java)
            package.write_bytes(self.package(hybrid, [("java/module.dex", data)]))
            inspect_archive(package)

            java_only = {
                key: value
                for key, value in self.manifest(schema_version=4, java_module=java).items()
                if key not in {"sdk_version", "min_sdk_minor", "entrypoint"}
            }
            package.write_bytes(zip_bytes([
                ("java/module.dex", data),
                ("manifest.json", (json.dumps(java_only) + "\n").encode()),
            ]))
            inspect_archive(package)

            for field in ("sdk_version", "min_sdk_minor", "entrypoint"):
                partial = java_only.copy()
                partial[field] = self.manifest()[field]
                package.write_bytes(zip_bytes([
                    ("java/module.dex", data),
                    ("manifest.json", (json.dumps(partial) + "\n").encode()),
                ]))

                with self.assertRaises(DevkitError):
                    inspect_archive(package)

    def test_validates_schema_five_host_fingerprint(self):
        data = self.dex_fixture()
        java = {
            "path": "java/module.dex",
            "entrypoint": "example.Module",
            "sha256": hashlib.sha256(data).hexdigest(),
            "api_version": 2,
            "min_api_minor": 0,
            "host_fingerprint": "f2ff60d61f95bb4c1587fecde7d32978e38bbd8c4e3aec2b8d0a9bcdd7e98e6b",
        }

        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "plugin.fgplugin"
            manifest = self.manifest(schema_version=5, java_module=java)
            package.write_bytes(self.package(manifest, [("java/module.dex", data)]))
            inspect_archive(package)

            for fingerprint in (None, "A" * 64, "0" * 64):
                invalid = java.copy()

                if fingerprint is None:
                    invalid.pop("host_fingerprint")
                else:
                    invalid["host_fingerprint"] = fingerprint

                package.write_bytes(self.package(
                    self.manifest(schema_version=5, java_module=invalid),
                    [("java/module.dex", data)],
                ))

                with self.assertRaises(DevkitError):
                    inspect_archive(package)

    def test_loads_java_only_project_without_python_metadata(self):
        project = """[plugin]
id = "example.java_only"
name = "Java Only"
version = "1.0.0"
description = "Java-only fixture"
author = "Tester"
declared_capabilities = []

[java]
sources = ["java/src"]
entrypoint = "example.Module"
min_api_minor = 0
"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fluxgram-plugin.toml").write_text(project)
            _, config, manifest = load_project(root)
            self.assertNotIn("python", config)
            self.assertEqual(manifest["schema_version"], 4)
            self.assertNotIn("entrypoint", manifest)
            self.assertNotIn("sdk_version", manifest)

    def test_rejects_newer_sdk_and_bundled_sdk(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "plugin.fgplugin"
            package.write_bytes(self.package(self.manifest(min_sdk_minor=12)))

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
                 b'"min_sdk_minor":10,"id":"example.test","name":"Test",'
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
        sources = api_sources_artifact()
        self.assertTrue(artifact.is_file())
        self.assertTrue(sources.is_file())
        self.assertEqual((metadata["api_version"], metadata["api_minor"]), (2, 0))
        self.assertEqual(hashlib.sha256(sources.read_bytes()).hexdigest(),
                         metadata["sources_jar_sha256"])

    def test_bundled_host_stubs_are_minimal_and_reproducible(self):
        artifact, metadata = host_stubs_artifact()
        source = (artifact.parent / "host-stubs-src/org/telegram/messenger/plugins/"
                  "ComposerInputRendererExtensions.java")
        self.assertEqual(
            hashlib.sha256(source.read_bytes()).hexdigest(),
            metadata["source_sha256"],
        )
        self.assertEqual(metadata["host_fingerprint"], metadata["source_sha256"])

        with zipfile.ZipFile(artifact) as archive:
            classes = {name for name in archive.namelist() if name.endswith(".class")}

        self.assertEqual(classes, {
            "org/telegram/messenger/plugins/ComposerInputRendererExtensions.class",
            "org/telegram/messenger/plugins/ComposerInputRendererExtensions$Factory.class",
            "org/telegram/messenger/plugins/ComposerInputRendererExtensions$Surface.class",
            "org/telegram/messenger/plugins/ComposerInputRendererExtensions$Renderer.class",
            "org/telegram/messenger/plugins/ComposerInputRendererExtensions$Frame.class",
            "org/telegram/messenger/plugins/ComposerInputRendererExtensions$ExpandedFrame.class",
            "org/telegram/messenger/plugins/ComposerInputRendererExtensions$Result.class",
        })

    def test_java_classpaths_include_opt_in_host_stubs(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            source = project / "java/src/example/Module.java"
            source.parent.mkdir(parents=True)
            source.write_text("package example; public class Module {}")
            tools = project / "tools"
            tools.mkdir()
            javac = tools / "javac"
            android = tools / "android.jar"
            d8 = tools / "d8"
            api, _ = api_artifact()
            stubs, _ = host_stubs_artifact()
            commands = []

            def fake_run(command, label):
                command = [Path(item) if isinstance(item, Path) else item for item in command]
                commands.append((command, label))

                if label == "javac":
                    classes = Path(command[command.index("-d") + 1])
                    target = classes / "example/Module.class"
                    target.parent.mkdir(parents=True)
                    target.write_bytes(b"class")
                else:
                    output = Path(command[command.index("--output") + 1])
                    (output / "classes.dex").write_bytes(self.dex_fixture())

            config = {
                "sources": ["java/src"],
                "entrypoint": "example.Module",
                "min_api_minor": 0,
                "host_stubs": True,
            }

            with patch("fluxgram_devkit.cli.find_java", return_value=javac), \
                    patch("fluxgram_devkit.cli.find_android",
                          return_value=(tools, android, tools, d8)), \
                    patch("fluxgram_devkit.cli.run", side_effect=fake_run):
                compile_java(project, config, None, None, None)

            javac_classpath = commands[0][0][commands[0][0].index("-cp") + 1].split(os.pathsep)
            d8_classpaths = [commands[1][0][index + 1]
                             for index, value in enumerate(commands[1][0])
                             if value == "--classpath"]
            self.assertEqual(javac_classpath, [str(android), str(api), str(stubs)])
            self.assertEqual(d8_classpaths, [api, stubs])

            commands.clear()
            config["host_stubs"] = False

            with patch("fluxgram_devkit.cli.find_java", return_value=javac), \
                    patch("fluxgram_devkit.cli.find_android",
                          return_value=(tools, android, tools, d8)), \
                    patch("fluxgram_devkit.cli.run", side_effect=fake_run):
                compile_java(project, config, None, None, None)

            self.assertNotIn(str(stubs), commands[0][0][commands[0][0].index("-cp") + 1])
            self.assertNotIn(stubs, commands[1][0])

    def test_build_never_packages_compile_classpath_jars(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "plugin"
            initialize_project(project, "java")
            data = self.dex_fixture("Lexample/plugin/Module;")

            with patch("fluxgram_devkit.cli.compile_java",
                       return_value=(data, Path("javac"), Path("sdk"), Path("tools"))):
                package = build_project(project)

            with zipfile.ZipFile(package) as archive:
                self.assertEqual(set(archive.namelist()), {"java/module.dex", "manifest.json"})
                manifest = json.loads(archive.read("manifest.json"))

            self.assertEqual(manifest["schema_version"], 5)
            self.assertIn("host_fingerprint", manifest["java_module"])

    def test_rejects_dex_that_bundles_host_class(self):
        for entrypoint in ("org.telegram.copied.HostClass", "org.fluxgram.HostClass"):
            with self.subTest(entrypoint=entrypoint):
                descriptor = "L" + entrypoint.replace(".", "/") + ";"
                data = self.dex_fixture(descriptor)
                java = {
                    "path": "java/module.dex",
                    "entrypoint": entrypoint,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "api_version": 2,
                    "min_api_minor": 0,
                }
                manifest = self.manifest(schema_version=3, java_module=java)

                with tempfile.TemporaryDirectory() as directory:
                    package = Path(directory) / "plugin.fgplugin"
                    package.write_bytes(self.package(manifest, [("java/module.dex", data)]))

                    with self.assertRaises(DevkitError):
                        inspect_archive(package)


if __name__ == "__main__":
    unittest.main()
