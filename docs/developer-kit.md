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
fluxgram-plugin doctor
```

To install the current checkout instead, run from the `devkit` directory:

```sh
python3 -m pip install .
fluxgram-plugin doctor
```

## Create a plugin

```sh
fluxgram-plugin init hello-plugin --template hybrid
cd hello-plugin
fluxgram-plugin build
fluxgram-plugin validate dist/example.hello_hybrid-1.0.0.fgplugin
```

Edit `fluxgram-plugin.toml` before publishing. The builder generates `manifest.json`, compiles Java with `--release 8`, creates one minSdk 24 DEX, inserts its SHA-256, and packages only explicitly listed Python files and assets.

The `python` and `hybrid` templates are stable lifecycle examples. The `java` template is a low-level E5 example using `ui.chat.composer.input.native_draw`; its transform policy remains in the external plugin. Do not add `fluxgram`, `fluxgram.py`, the installed `fluxgram-plugin` SDK, either bundled JAR, `.venv`, build output, or local credentials to the package.

## Project file

`fluxgram-plugin.toml` separates package metadata from source locations. `python.files` and `assets.files` are archive-relative allowlists under their corresponding roots. Java source directories are compiled recursively. A project must declare `[python]`, `[java]`, or both. For Java-only packages, remove `[python]` and `plugin.min_sdk_minor`; no empty Python entrypoint is generated. `[java].host_stubs` is an optional boolean and defaults to `false`.

`min_sdk_minor` declares the oldest Python host API used by the plugin. `java.min_api_minor` declares the oldest Java API used by the Java module. The bundled artifact provides Python host API 2.9 metadata and Java API 1.21. Lower a minimum only after testing against that older client API.

Schema 4 uses the existing root Python `PluginEntry` fields only when `[python]` is present and the existing `java_module` object only when `[java]` is present. With `host_stubs = true`, Java compilation and D8 resolution additionally use `fluxgram-host-stubs.jar`, and the builder emits schema 5 with the exact bundled `java_module.host_fingerprint`. The stubs expose only compile-time signatures for E5 `ComposerInputRendererExtensions`; they are tied to one host contract and are never packaged into `.fgplugin`. Java-only packages currently have no declarative settings schema; their settings page is empty. `declared_capabilities` is historical metadata and does not grant or restrict process access. `access` is generated as `full` because no sandboxed mode exists.

## Commands

```sh
fluxgram-plugin init PATH [--template {python,java,hybrid}]
fluxgram-plugin new PATH [--template {python,java,hybrid}]
fluxgram-plugin doctor [--java-home PATH] [--android-sdk PATH] [--build-tools VERSION]
fluxgram-plugin build [PROJECT] [--output FILE] [--java-home PATH] [--android-sdk PATH] [--build-tools VERSION]
fluxgram-plugin validate FILE
fluxgram-plugin install ARCHIVE --device SERIAL [--package PACKAGE] [--adb PATH] [--enable]
fluxgram-plugin reload PROJECT --device SERIAL [--package PACKAGE] [--adb PATH]
fluxgram-plugin logs --device SERIAL --plugin ID [--package PACKAGE] [--adb PATH]
```

Standalone validation catches manifest, archive, digest, compatibility, path, size, duplicate, symlink, DEX version, and entrypoint errors. The FluxGram installer remains the final source of truth and repeats package and DEX validation before installation.

Device commands require exactly the requested online ADB device and successful `run-as` access to the debug package. The default package is `org.fluxgram.messenger.beta`. Install and reload stream archive bytes directly to the debug content provider, trigger the operation by SHA-256, and require a bounded successful JSON result. Reload builds first and requests enable. Logs resolve the current process ID and stream only `FluxGramPython` and `FluxGramDevBridge`; `--plugin` filters local output by `[plugin.id]`.

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
