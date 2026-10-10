# FluxGram SDK

Public Python interfaces, models, and typing information for FluxGram plugins.

The package can be imported on CPython 3.11 or newer for development and static analysis. Runtime services require the Android client and are provided by FluxGram through Chaquopy.

Do not include this package in a `.fgplugin` archive. FluxGram provides the matching SDK at runtime.

## Development

Run the standalone checks from the repository root:

```sh
python3 -B -m unittest discover -s tests
uv build --offline
```

The Android client pins this repository as a submodule and runs additional integration tests against `fluxgram_runtime` and the Java host.
