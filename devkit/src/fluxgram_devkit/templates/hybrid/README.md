# Hello Hybrid Plugin

Create the development environment and select `.venv` as the Python interpreter in your editor:

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
fluxgram-plugin prepare-ide
fluxgram-plugin doctor
```

With Pyright or Pylance enabled, `pyrightconfig.json` and the installed `fluxgram-plugin` package provide Python completion, type information, and behavioral hover documentation. Open this directory as a Gradle project in Android Studio or IntelliJ IDEA for the Java API classpath and attached documented sources. `fluxgram-plugin build` remains the authoritative package builder.

```sh
fluxgram-plugin build
fluxgram-plugin validate dist/example.hello_hybrid-1.0.0.fgplugin
```
