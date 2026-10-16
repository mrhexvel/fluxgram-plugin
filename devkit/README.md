# FluxGram Plugin Devkit

Installable command-line tools, stable Java API 1.22, and fingerprinted E5 host stubs for building external FluxGram plugins.

```sh
python3 -m pip install fluxgram-plugin-devkit
fluxgram-plugin doctor
fluxgram-plugin init my-plugin --template hybrid
```

Devkit 1.5 creates Python, Java, and hybrid projects. The Java template demonstrates the low-level composer native-draw replacement and therefore produces a host-bound schema 5 package. `install`, `reload`, and `logs` use the debug device bridge without remote shell paths or shell interpolation.

Java API 1.22 adds lifecycle-managed ART method hooks. A Java plugin registers an exact reflected `Method` or `Constructor` through `JavaPluginContext.registerMethodHook`. Before and after callbacks run synchronously on the target method's thread, must not block or call Python, and receive an isolated argument array whose changes are committed only after a successful callback and active-generation check. The callback can set the result or throwable and can invoke the original once from the before phase. Managed registrations are ordered by priority, plugin ID, and registration sequence; after callbacks run in reverse order. The returned handle can be closed explicitly, while disable, unload, failed startup, and removal perform automatic unhook.

Direct AliuHook/XposedBridge access remains possible for full-trust Java plugins, but direct registrations are outside FluxGram lifecycle tracking and must retain and close every `Unhook` themselves.

See the [developer kit guide](https://github.com/mrhexvel/fluxgram-plugin/blob/main/docs/developer-kit.md) for requirements, trust boundaries, project format, commands, and testing guidance.
