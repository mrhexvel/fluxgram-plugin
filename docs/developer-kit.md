# FluxGram Plugin Developer Kit

The developer kit creates schema 2 Python packages and schema 3 Python plus Java packages without copying either SDK into the plugin archive.

Plugins run as trusted code inside the FluxGram process. They can access Android, Java, Telegram internals, files, and the network with the application's privileges. SDK facades and package validation are reliability and compatibility contracts, not a security sandbox. Installing a package imports its Python entrypoint for migration checks before normal plugin enable.

## Requirements

- Python 3.11 or newer.
- JDK 17 or 21 for Java plugins.
- Android SDK platform 36.
- Android build-tools 35 or 36.

Set `JAVA_HOME` and `ANDROID_SDK_ROOT`, or pass explicit paths to the commands.

## Install

From the `devkit` directory:

```sh
python3 -m pip install .
fluxgram-plugin doctor
```

## Create a plugin

```sh
fluxgram-plugin init hello-plugin
cd hello-plugin
fluxgram-plugin build
fluxgram-plugin validate dist/example.hello_plugin-1.0.0.fgplugin
```

Edit `fluxgram-plugin.toml` before publishing. The builder generates `manifest.json`, compiles Java with `--release 8`, creates one minSdk 24 DEX, inserts its SHA-256, and packages only explicitly listed Python files and assets.

The template is intentionally a lifecycle example rather than a ready-made visual effect. Plugin behavior belongs in the external package. Do not add `fluxgram`, `fluxgram.py`, the installed `fluxgram-plugin` SDK, the Java API JAR, `.venv`, build output, or local credentials to the package.

## Project file

`fluxgram-plugin.toml` separates package metadata from source locations. `python.files` and `assets.files` are archive-relative allowlists under their corresponding roots. Java source directories are compiled recursively.

`min_sdk_minor` declares the oldest Python host API used by the plugin. `java.min_api_minor` declares the oldest Java API used by the Java module. The bundled artifact provides Python host API 2.9 metadata and Java API 1.19. Lower a minimum only after testing against that older client API.

Schema 3 still requires a root Python `PluginEntry`; Java-only packages are not supported. `declared_capabilities` is historical metadata and does not grant or restrict process access. `access` is generated as `full` because no sandboxed mode exists.

## Commands

```sh
fluxgram-plugin init PATH
fluxgram-plugin doctor [--java-home PATH] [--android-sdk PATH] [--build-tools VERSION]
fluxgram-plugin build [PROJECT] [--output FILE] [--java-home PATH] [--android-sdk PATH] [--build-tools VERSION]
fluxgram-plugin validate FILE
```

Standalone validation catches manifest, archive, digest, compatibility, path, size, duplicate, symlink, DEX version, and entrypoint errors. The FluxGram installer remains the final source of truth and repeats package and DEX validation before installation.

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
