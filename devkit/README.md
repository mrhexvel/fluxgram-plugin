# FluxGram Plugin Devkit

Installable command-line tools, stable Java API 2.0, and fingerprinted E5 host stubs for building external FluxGram plugins.

```sh
python3 -m pip install fluxgram-plugin-devkit
fluxgram-plugin doctor
fluxgram-plugin init my-plugin --template hybrid
```

Devkit 2.1 creates editor-ready Python, Java, and hybrid projects. Python templates include a Pyright configuration and development requirements. Java templates include a Gradle project whose compile-only classpath uses generated files under `.fluxgram/ide`. Run `fluxgram-plugin prepare-ide` after cloning a project or updating the devkit to restore those ignored files. The Java template demonstrates the low-level composer native-draw replacement and therefore produces a host-bound schema 5 package. `install`, `reload`, and `logs` use the debug device bridge without remote shell paths or shell interpolation.

Java API 2.0 includes lifecycle-managed ART method hooks. A Java plugin registers an exact reflected `Method` or `Constructor` through `JavaPluginContext.registerMethodHook`. Before and after callbacks run synchronously on the target method's thread, must not block or call Python, and receive an isolated argument array whose changes are committed only after a successful callback and active-generation check. The callback can set the result or throwable and can invoke the original once from the before phase. Managed registrations are ordered by priority, plugin ID, and registration sequence; after callbacks run in reverse order. The returned handle can be closed explicitly, while disable, unload, failed startup, and removal perform automatic unhook.

Java API 2.0 moves the public ABI to `org.fluxgram`. API 1.x plugins use the former package namespace and must be rebuilt for 2.0. `registerChatSelectionAction` provides one host-owned action in the ordinary chat selection toolbar. The callback receives an immutable server-backed message identity with message, dialog, and optional sender IDs. Availability and invocation run synchronously on the main thread, so implementations must remain fast and must not call Python or perform I/O.

Direct AliuHook/XposedBridge access remains possible for full-trust Java plugins, but direct registrations are outside FluxGram lifecycle tracking and must retain and close every `Unhook` themselves.

See the [developer kit guide](https://github.com/mrhexvel/fluxgram-plugin/blob/main/docs/developer-kit.md) for requirements, trust boundaries, project format, commands, and testing guidance.
