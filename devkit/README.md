# FluxGram Plugin Devkit

Installable command-line tools, stable Java API 1.21, and fingerprinted E5 host stubs for building external FluxGram plugins.

```sh
python3 -m pip install fluxgram-plugin-devkit
fluxgram-plugin doctor
fluxgram-plugin init my-plugin --template hybrid
```

Devkit 1.2 creates Python, Java, and hybrid projects. The Java template demonstrates the low-level composer native-draw replacement and therefore produces a host-bound schema 5 package. `install`, `reload`, and `logs` use the debug device bridge without remote shell paths or shell interpolation.

See the [developer kit guide](https://github.com/mrhexvel/fluxgram-plugin/blob/main/docs/developer-kit.md) for requirements, trust boundaries, project format, commands, and testing guidance.
