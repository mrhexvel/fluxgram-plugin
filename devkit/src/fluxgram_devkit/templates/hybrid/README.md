# Hello Hybrid Plugin

Create the development environment and select `.venv` as the Python interpreter in your editor:

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
fp prepare-ide
fp doctor
```

With Pyright or Pylance enabled, `pyrightconfig.json` and the installed `fluxgram-plugin` package provide Python completion, type information, and behavioral hover documentation. Open this directory as a Gradle project in Android Studio or IntelliJ IDEA for the Java API classpath and attached documented sources. `fp build` remains the authoritative package builder.

```sh
fp build
fp validate dist/example.hello_hybrid-1.0.0.fgplugin
```
