# Composer Renderer

Create a development environment and restore the generated editor classpath:

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
fp prepare-ide
fp doctor
```

Open this directory as a Gradle project in Android Studio or IntelliJ IDEA. The Gradle model provides completion and attached documented sources for stable FluxGram Java API 2.1, plus the fingerprinted composer host stubs. `fp build` remains the authoritative package builder.

```sh
fp build
fp validate dist/example.composer_renderer-1.0.0.fgplugin
```
