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
    settings_schema = ()
    storage_schema_version = 0

    def __init__(self, plugin_id: str):
        runtime = create_runtime(plugin_id)

        self._plugin_id = plugin_id
        self._call_ui = runtime.call_ui
        self._log = runtime.log
        self.settings = Settings(self.settings_schema)
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
        return self._plugin_id

    def log(self, message: str) -> None:
        self._log(self.id, message)

    def on_setting_changed(self, key: str, value: bool | str | int) -> None:
        pass

    def on_load(self) -> None:
        pass

    def on_unload(self) -> None:
        pass

    @classmethod
    def migrate_storage(cls, migration: StorageMigration) -> None:
        pass

    def before_send(self, event: BeforeSendEvent) -> None:
        pass

    def transform_outgoing_text(
            self,
            request: OutgoingTextTransformRequest,
    ) -> str | None:
        return self.commands._dispatch(request)
