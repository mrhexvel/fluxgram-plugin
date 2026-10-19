# FluxGram Plugin Developer Kit

The developer kit creates schema 4 Python-only, Java-only, and hybrid packages without copying either SDK into the plugin archive. Projects which opt into build-specific host stubs produce schema 5 packages with a host fingerprint. Existing schema 2 Python, schema 3 hybrid, and schema 4 packages remain valid.

Plugins run as trusted code inside the FluxGram process. They can access Android, Java, Telegram internals, files, and the network with the application's privileges. SDK facades and package validation are reliability and compatibility contracts, not a security sandbox. Installing a package with a Python module imports its entrypoint for migration checks before normal plugin enable. Java-only packages are validated without loading their DEX until enable.

## Requirements

- Python 3.11 or newer.
- JDK 17 or 21 for Java plugins.
- Android SDK platform 36.
- Android build-tools 35 or 36.

Set `JAVA_HOME` and `ANDROID_SDK_ROOT`, or pass explicit paths to the commands.

## Install

Install the published developer kit:

```sh
python3 -m pip install fluxgram-plugin-devkit
fp doctor
```

`fp` is the short command. The longer `fluxgram-plugin` name remains available for compatibility.

To install the current checkout instead, run from the `devkit` directory:

```sh
python3 -m pip install .
fp doctor
```

## Create a plugin

```sh
fp init hello-plugin --template hybrid
cd hello-plugin
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
fp build
fp validate dist/example.hello_hybrid-1.0.0.fgplugin
```

Edit `fluxgram-plugin.toml` before publishing. The builder generates `manifest.json`, compiles Java with `--release 8`, creates one minSdk 24 DEX, inserts its SHA-256, and packages only explicitly listed Python files and assets.

Open the generated directory as the editor project. Select `.venv` as the Python interpreter in VS Code or PyCharm. Python and hybrid templates include `pyrightconfig.json`, while Java and hybrid templates include a Gradle compile-only project for Android Studio, IntelliJ IDEA, or a Java language server. `fluxgram-plugin.toml` points to a generated JSON Schema for completion and validation in TOML editors which support schema directives.

Files under `.fluxgram/ide` are generated and ignored. Restore them after cloning or updating the devkit:

```sh
fp prepare-ide
```

The Gradle project exists only to provide the editor classpath and quick Java compilation. The stable Java API is resolved from a generated local Maven repository so IntelliJ-based editors attach its matching sources and Javadocs without network access. `fp build` remains the authoritative package builder and does not include IDE files or SDK artifacts in `.fgplugin`.

The `python` and `hybrid` templates are stable lifecycle examples. The `java` template is a low-level E5 example using `ui.chat.composer.input.native_draw`; its transform policy remains in the external plugin. Do not add `fluxgram`, `fluxgram.py`, the installed `fluxgram-plugin` SDK, either bundled JAR, `.venv`, build output, or local credentials to the package.

## Project file

`fluxgram-plugin.toml` separates package metadata from source locations. `python.files` and `assets.files` are archive-relative allowlists under their corresponding roots. Java source directories are compiled recursively. A project must declare `[python]`, `[java]`, or both. For Java-only packages, remove `[python]` and `plugin.min_sdk_minor`; no empty Python entrypoint is generated. `[java].host_stubs` is an optional boolean and defaults to `false`.

`min_sdk_minor` declares the oldest Python host API used by the plugin. `java.min_api_minor` declares the oldest Java API used by the Java module. The bundled artifact provides Python host API 2.12 metadata and Java API 2.1. Lower a minimum only after testing against that older client API.

Schema 4 uses the existing root Python `PluginEntry` fields only when `[python]` is present and the existing `java_module` object only when `[java]` is present. With `host_stubs = true`, Java compilation and D8 resolution additionally use `fluxgram-host-stubs.jar`, and the builder emits schema 5 with the exact bundled `java_module.host_fingerprint`. The stubs expose only compile-time signatures for E5 `ComposerInputRendererExtensions`; they are tied to one host contract and are never packaged into `.fgplugin`. Java-only packages have no declarative settings schema, but Java API 2.1 can provide lifecycle-managed custom settings blocks. `declared_capabilities` is historical metadata and does not grant or restrict process access. `access` is generated as `full` because no sandboxed mode exists.

## Commands

```sh
fp init PATH [--template {python,java,hybrid}]
fp new PATH [--template {python,java,hybrid}]
fp prepare-ide [PROJECT]
fp doctor [--java-home PATH] [--android-sdk PATH] [--build-tools VERSION]
fp build [PROJECT] [--output FILE] [--java-home PATH] [--android-sdk PATH] [--build-tools VERSION]
fp validate FILE
fp install ARCHIVE --device SERIAL [--package PACKAGE] [--adb PATH] [--enable]
fp reload PROJECT --device SERIAL [--package PACKAGE] [--adb PATH]
fp logs --device SERIAL --plugin ID [--package PACKAGE] [--adb PATH]
```

Standalone validation catches manifest, archive, digest, compatibility, path, size, duplicate, symlink, DEX version, and entrypoint errors. The FluxGram installer remains the final source of truth and repeats package and DEX validation before installation.

Device commands require exactly the requested online ADB device. The default package is the release client, `org.fluxgram.messenger`; pass `--package org.fluxgram.messenger.beta` for a debug installation. In a release client, first enable the temporary 15-minute developer session under FluxGram Settings → Plugins. Install and reload stream archive bytes directly to a `DUMP`-protected shell-only content provider, trigger the operation by SHA-256, and require a bounded successful JSON result. Every non-streaming ADB command has a 120-second timeout; after a timeout, verify the device connection, restart the ADB server, and retry. The session is not persisted and ends on expiry, process death, or disabling the plugin system. Reload builds first and requests enable. Logs do not require the developer session: their device and process checks use the same timeout, after which logcat streams until interrupted. The stream includes only `FluxGramPython` and `FluxGramDevBridge`; `--plugin` filters local output by `[plugin.id]`.

## Test Python behavior

Use the public runtime harness without Android:

```python
from fluxgram.testing import PluginHarness
from main import PluginEntry


def test_lifecycle():
    with PluginHarness(PluginEntry) as harness:
        assert harness.plugin.settings.get("enabled")
```

Run project tests with the same Python 3.11 baseline used by FluxGram plugins. Test Java modules in a release/R8 client before distribution, particularly when using composer rendering APIs.
