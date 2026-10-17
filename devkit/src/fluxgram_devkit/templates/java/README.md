# Composer Renderer

Create a development environment and restore the generated editor classpath:

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
fluxgram-plugin prepare-ide
fluxgram-plugin doctor
```

Open this directory as a Gradle project in Android Studio or IntelliJ IDEA. The Gradle model provides completion and attached documented sources for stable FluxGram Java API 2.0, plus the fingerprinted composer host stubs. `fluxgram-plugin build` remains the authoritative package builder.

```sh
fluxgram-plugin build
fluxgram-plugin validate dist/example.composer_renderer-1.0.0.fgplugin
```
