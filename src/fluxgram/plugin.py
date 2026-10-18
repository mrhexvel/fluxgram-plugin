from enum import Enum, auto

from .commands import Commands
from .events import BeforeSendEvent, Events
from ._runtime import create_runtime
from .settings import Settings
from .storage import Storage, StorageMigration
from .tasks import Tasks
from .transforms import OutgoingTextTransformRequest
from .ui import UI


class _PluginState(Enum):
    INITIALIZED = auto()
    ACTIVE = auto()
    CLOSING = auto()
    CLOSED = auto()


class Plugin:
    """Base class for one lifecycle-managed plugin runtime instance.

    Subclasses declare settings and storage schemas as class attributes. The host
    creates one instance per active generation and owns all exposed services.
    """

    settings_schema = ()
    storage_schema_version = 0

    def __init__(self, plugin_id: str):
        runtime = create_runtime(plugin_id)

        self._plugin_id = plugin_id
        self._call_ui = runtime.call_ui
        self._log = runtime.log
        self.settings = Settings(self.settings_schema)
        self.settings._set_condition_error_handler(
            lambda key, name, error: self.log(
                f"Setting {key} {name} condition failed: "
                f"{type(error).__name__}: {str(error)[:160]}"
            )
        )
        self.commands = Commands()
        self.events = Events(runtime.events)
        self.events._set_error_handler(
            lambda name, error: self.log(
                f"{name} callback failed: {type(error).__name__}: {error}"
            )
        )
        self.client = runtime.client
        self.storage = Storage(runtime.storage)
        self.tasks = Tasks(runtime.tasks)
        self.ui = UI(runtime.ui_thread, runtime.ui_services)
        self._cleanup_done = set()
        self._state = _PluginState.INITIALIZED

    def _bind_runtime(self, host, generation: int) -> None:
        if self._state is not _PluginState.INITIALIZED:
            raise RuntimeError("Plugin runtime is already bound or closed")

        self.settings._bind_runtime(host, generation)
        self.client._bind_runtime(host, generation)
        self.storage._bind_runtime(host, generation)
        self.tasks._bind_runtime(host, generation)
        self.ui._bind_runtime(host, generation)
        self.events.bind_runtime(host, generation)
        self._state = _PluginState.ACTIVE

    def _unload(self) -> None:
        if self._state is _PluginState.CLOSED:
            return

        self._state = _PluginState.CLOSING
        steps = (
            ("tasks", self.tasks._close),
            ("ui", lambda: self._call_ui(self.ui._close)),
            ("client", self.client._close),
            ("events", lambda: self._call_ui(self.events.close)),
            ("storage", self.storage._close),
            ("commands", self.commands._close),
            ("on_unload", self.on_unload),
        )
        errors = []

        for name, close in steps:
            if name in self._cleanup_done:
                continue

            try:
                close()
                self._cleanup_done.add(name)
            except BaseException as error:
                errors.append((name, error))

        if errors:
            names = ", ".join(name for name, _ in errors)
            raise RuntimeError(f"Plugin cleanup failed: {names}") from errors[0][1]

        self._state = _PluginState.CLOSED

    @property
    def id(self) -> str:
        """Return the immutable manifest plugin ID for this runtime."""

        return self._plugin_id

    def log(self, message: str) -> None:
        """Write a plugin-attributed message to the client log."""

        self._log(self.id, message)

    def on_setting_changed(self, key: str, value: bool | str | int) -> None:
        """Handle a setting change on the plugin worker thread.

        This fallback hook runs only when the setting has no ``on_changed``
        callback. It is not called for initial values.
        """

        pass

    def on_load(self) -> None:
        """Initialize the plugin on its worker thread after services are bound."""

        pass

    def on_unload(self) -> None:
        """Release plugin-owned resources on the worker thread.

        Managed SDK resources are closed first. The hook may be retried if
        cleanup fails, so implementations should be idempotent.
        """

        pass

    @classmethod
    def migrate_storage(cls, migration: StorageMigration) -> None:
        """Migrate host-managed storage before a runtime instance is created.

        Changes made through ``migration`` commit atomically with the target
        schema version. Raising aborts the migration and plugin startup.
        """

        pass

    def before_send(self, event: BeforeSendEvent) -> None:
        """Observe legacy send metadata on the plugin worker thread.

        The event contains no message text and this hook cannot cancel or alter
        the send operation.
        """

        pass

    def transform_outgoing_text(
            self,
            request: OutgoingTextTransformRequest,
    ) -> str | None:
        """Transform an outgoing plain-text send on the plugin worker thread.

        Return a string to replace the text or ``None`` to preserve the current
        pipeline value. The default implementation dispatches registered
        commands. The host applies timeout, size, lifecycle, and stale-request
        fallback checks before using a result.
        """

        return self.commands._dispatch(request)
