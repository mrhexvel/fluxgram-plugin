# Hello Python Plugin

Create the development environment and select `.venv` as the Python interpreter in your editor:

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

With Pyright or Pylance enabled, `pyrightconfig.json` and the installed `fluxgram-plugin` package provide completion, type information, and behavioral hover documentation. Restore the generated TOML schema after cloning or updating the devkit:

```sh
fp prepare-ide
```

Build and validate the package:

```sh
fp build
fp validate dist/example.hello_python-1.0.0.fgplugin
```
