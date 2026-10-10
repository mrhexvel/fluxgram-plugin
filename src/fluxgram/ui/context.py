class UI:
    def __init__(self, runtime=None, services=None):
        self._closed = False
        self._is_active = lambda: False
        self._cleanup_callbacks = []
        self._runtime = runtime
        self._services = services

    def _bind_runtime(self, host, generation):
        self._is_active = lambda: host.isGenerationActive(generation)

    def _active(self):
        return not self._closed and self._is_active()

    def _require_ui(self):
        if not self._active():
            raise RuntimeError("Plugin UI is inactive")

        if self._runtime is None or not self._runtime.is_ui_thread():
            raise RuntimeError("Plugin UI must be called on the UI thread")

    def _register_cleanup(self, callback):
        if self._closed:
            raise RuntimeError("Plugin UI is inactive")

        self._cleanup_callbacks.append(callback)

    def _require_services(self):
        if self._services is None:
            raise RuntimeError("Plugin UI runtime is unavailable")

        return self._services

    def _dispose(self):
        errors = []

        for callback in reversed(tuple(self._cleanup_callbacks)):
            try:
                callback()
            except Exception as error:
                errors.append(error)
            else:
                self._cleanup_callbacks.remove(callback)

        if errors:
            raise RuntimeError("Plugin UI cleanup failed") from errors[0]

    def _close(self):
        self._closed = True
        self._is_active = lambda: False

        if self._runtime is not None and self._runtime.is_ui_thread():
            self._dispose()
        elif self._runtime is not None:
            self._runtime.post(self._dispose)
        elif self._cleanup_callbacks:
            raise RuntimeError("Plugin UI runtime is unavailable")
