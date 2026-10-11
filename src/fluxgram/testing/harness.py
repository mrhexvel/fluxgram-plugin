from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from .._runtime import RuntimeBindings, runtime_scope
from ..events import (
    BeforeSendEvent,
    EventContext,
    MessageReceivedEvent,
    PluginSettingChangedEvent,
)
from ..models import ChatInfo, ChatNotifications, NotificationOverride
from ..plugin import Plugin
from ..settings import SelectSetting, SliderSetting, SwitchSetting, TextSetting
from ..storage import Storage, StorageMigrationError, _migrate_plugin_storage
from .storage import HarnessStorageBackend
from .scheduler import HarnessTaskScheduler


PluginType = TypeVar("PluginType", bound=Plugin)


@dataclass(frozen=True)
class BulletinCall:
    title: str
    text: str
    on_click: Callable[[], Any] | None
    duration: int
    top: bool


@dataclass(frozen=True)
class NotificationCall:
    event: MessageReceivedEvent
    respect_mute: bool
    show_in_current_chat: bool
    duration: int


@dataclass(frozen=True)
class NavigationCall:
    account: int
    dialog_id: int
    topic_id: int
    message_id: int


@dataclass(frozen=True)
class ChatLookup:
    account: int
    dialog_id: int
    topic_id: int


class _Host:
    def __init__(self, generation: int, values: dict[str, Any]):
        self._generation = generation
        self._values = values
        self.message_received_active = False

    def invalidate(self) -> None:
        self._generation = None
        self.message_received_active = False

    def isGenerationActive(self, generation: int) -> bool:
        return self._generation == generation

    def readBooleanSetting(self, key: str, fallback: bool) -> bool:
        value = self._values.get(key, fallback)

        return value if type(value) is bool else fallback

    def readStringSetting(self, key: str, fallback: str) -> str:
        value = self._values.get(key, fallback)

        return value if isinstance(value, str) else fallback

    def readIntSetting(self, key: str, fallback: int) -> int:
        value = self._values.get(key, fallback)

        return value if type(value) is int else fallback

    def setMessageReceivedSubscriptionActive(
            self,
            generation: int,
            active: bool,
    ) -> None:
        if self.isGenerationActive(generation):
            self.message_received_active = active


class _UIThread:
    def __init__(self):
        self._depth = 0
        self._pending = deque()

    def is_ui_thread(self) -> bool:
        return self._depth > 0

    def post(self, callback: Callable[[], Any]) -> None:
        self._pending.append(callback)

    def call(self, callback: Callable[[], Any]) -> Any:
        self._depth += 1

        try:
            return callback()
        finally:
            self._depth -= 1

    def call_worker(self, callback: Callable[[], Any]) -> Any:
        depth = self._depth
        self._depth = 0

        try:
            return callback()
        finally:
            self._depth = depth

    def drain(self) -> None:
        while self._pending:
            self.call(self._pending.popleft())

    @property
    def pending_count(self) -> int:
        return len(self._pending)


class _Subscription:
    def __init__(self, bus, name: str, callback: Callable[[Any], Any], thread: str):
        self._bus = bus
        self.name = name
        self.callback = callback
        self.thread = thread
        self.closed = False

    def unsubscribe(self) -> None:
        if self.closed:
            return

        self.closed = True
        self.callback = None


class HarnessEvents:
    SUPPORTED = ("message.received", "notification.received")

    def __init__(self, ui_thread: _UIThread):
        self._ui_thread = ui_thread
        self._is_active = lambda: False
        self._closed = False
        self.subscriptions = []

    def bind_runtime(self, host: _Host, generation: int) -> None:
        self._is_active = lambda: host.isGenerationActive(generation)

    def subscribe(
            self,
            name: str,
            callback: Callable[[Any], Any],
            *,
            thread: str = "ui",
    ) -> _Subscription:
        if self._closed:
            raise RuntimeError("Plugin event bus is closed")

        if name not in self.SUPPORTED:
            raise ValueError(f"Unsupported event: {name}")

        if thread != "ui":
            raise ValueError("This event currently supports only thread='ui'")

        if not callable(callback):
            raise TypeError("callback must be callable")

        subscription = _Subscription(self, name, callback, thread)
        self.subscriptions.append(subscription)

        return subscription

    def emit(self, name: str, event: MessageReceivedEvent) -> tuple[Any, ...]:
        if name not in self.SUPPORTED:
            raise ValueError(f"Unsupported event: {name}")

        if not isinstance(event, MessageReceivedEvent):
            raise TypeError("event must be a MessageReceivedEvent")

        if self._closed or not self._is_active():
            return ()

        results = []

        for subscription in tuple(self.subscriptions):
            if subscription.closed or subscription.name != name:
                continue

            results.append(self._ui_thread.call(lambda: subscription.callback(event)))

        return tuple(results)

    def close(self) -> None:
        if self._closed:
            return

        self._closed = True
        self._is_active = lambda: False

        for subscription in tuple(self.subscriptions):
            subscription.unsubscribe()

    @property
    def active_count(self) -> int:
        return sum(not subscription.closed for subscription in self.subscriptions)


class HarnessChats:
    def __init__(self, require_active: Callable[[], None], ui_thread: _UIThread):
        self._require_active = require_active
        self._ui_thread = ui_thread
        self._items = {}
        self.calls = []

    def add(self, value: ChatInfo, *, account: int = 0, topic_id: int = 0) -> None:
        if not isinstance(value, ChatInfo):
            raise TypeError("value must be a ChatInfo")

        self._items[(account, value.id, topic_id)] = value

    def get(self, *, account: int, dialog_id: int, topic_id: int = 0) -> ChatInfo:
        self._require_active()

        if not self._ui_thread.is_ui_thread():
            raise RuntimeError(
                "client.chats.get must run on the UI thread; "
                "use it inside a message.received callback with thread='ui'"
            )

        lookup = ChatLookup(account, dialog_id, topic_id)
        self.calls.append(lookup)
        value = self._items.get((account, dialog_id, topic_id))

        if value is not None:
            return value

        return ChatInfo(
            id=dialog_id,
            title=None,
            kind="unknown",
            is_forum=None,
            archived=None,
            notifications=ChatNotifications(
                chat_override=NotificationOverride("unknown"),
                topic_override=None,
                effective_muted=None,
            ),
        )


class HarnessClient:
    def __init__(self, ui_thread: _UIThread):
        self._closed = False
        self._is_active = lambda: False
        self.chats = HarnessChats(self._require_active, ui_thread)

    def _bind_runtime(self, host: _Host, generation: int) -> None:
        if self._closed:
            raise RuntimeError("Plugin client is closed")

        self._is_active = lambda: host.isGenerationActive(generation)

    def _require_active(self) -> None:
        if self._closed or not self._is_active():
            raise RuntimeError("Plugin client is inactive")

    def _close(self) -> None:
        self._closed = True
        self._is_active = lambda: False


class _BulletinPresenter:
    def __init__(self, ui, calls: list[BulletinCall]):
        self._ui = ui
        self._calls = calls
        self.dismissed = False

    def simple(
            self,
            title: str,
            text: str,
            *,
            on_click: Callable[[], Any] | None = None,
            duration: int = 4000,
            top: bool = True,
    ) -> bool:
        self._ui._require_ui()
        self.dismissed = False
        self._calls.append(BulletinCall(title, text, on_click, duration, top))

        return True

    def dismiss(self) -> None:
        self.dismissed = True


class _NotificationsService:
    def __init__(self, ui, calls: list[NotificationCall]):
        self._ui = ui
        self._calls = calls
        self.closed = False
        ui._register_cleanup(self.close)

    def show_message(
            self,
            event: MessageReceivedEvent,
            *,
            respect_mute: bool = True,
            show_in_current_chat: bool = False,
            duration: int = 5000,
    ) -> bool:
        self._ui._require_ui()

        if self.closed:
            raise RuntimeError("Plugin notifications are closed")

        self._calls.append(NotificationCall(
            event,
            respect_mute,
            show_in_current_chat,
            duration,
        ))

        return True

    def close(self) -> None:
        self.closed = True


class HarnessUI:
    def __init__(self):
        self.bulletins = []
        self.notifications = []
        self.navigation = []


class _UIServices:
    def __init__(self, records: HarnessUI):
        self._records = records
        self.presenters = []
        self.notification_services = []

    def create_bulletin_presenter(self, ui) -> _BulletinPresenter:
        presenter = _BulletinPresenter(ui, self._records.bulletins)
        self.presenters.append(presenter)

        return presenter

    def create_notifications(self, ui) -> _NotificationsService:
        service = _NotificationsService(ui, self._records.notifications)
        self.notification_services.append(service)

        return service

    def open_chat(
            self,
            account: int,
            dialog_id: int,
            topic_id: int,
            message_id: int,
    ) -> bool:
        self._records.navigation.append(NavigationCall(
            account,
            dialog_id,
            topic_id,
            message_id,
        ))

        return True


class HarnessSettings:
    def __init__(self, harness, values: dict[str, Any]):
        self._harness = harness
        self._values = values
        self._pending = OrderedDict()

    @property
    def values(self) -> dict[str, Any]:
        return dict(self._values)

    def get(self, key: str) -> Any:
        return self._harness.plugin.settings.get(key)

    def set(self, key: str, value: Any, *, dispatch: bool = True) -> None:
        item = self._harness.plugin.settings._schema.get(key)

        if item is None:
            raise KeyError(key)

        self._validate(item, value)
        self._values[key] = value
        self._pending[key] = (
            value,
            EventContext(
                account=self._harness.account,
                owner_id=self._harness.owner_id,
                generation=self._harness.generation,
            ),
        )

        if dispatch:
            self.flush()

    def set_raw(self, key: str, value: Any) -> None:
        if key not in self._harness.plugin.settings._schema:
            raise KeyError(key)

        self._values[key] = value

    def flush(self) -> None:
        while self._pending:
            key, pending = self._pending.popitem(last=False)
            value, context = pending

            if not self._harness.active:
                continue

            if self._harness.owner_for(context.account) != context.owner_id:
                continue

            self._harness.run_worker(
                lambda: self._dispatch(key, value, context)
            )

    def _dispatch(self, key, value, context) -> None:
        event = PluginSettingChangedEvent(
            context=context,
            key=key,
            value=value,
        )
        self._harness.plugin.events._dispatch(event)
        self._harness.plugin.settings._dispatch_changed(
            self._harness.plugin,
            key,
            value,
        )

    def clear_pending(self) -> None:
        self._pending.clear()

    @staticmethod
    def _validate(item, value: Any) -> None:
        if isinstance(item, SwitchSetting):
            valid = type(value) is bool
        elif isinstance(item, SelectSetting):
            valid = isinstance(value, str) and value in dict(item.options)
        elif isinstance(item, SliderSetting):
            valid = (
                type(value) is int
                and item.minimum <= value <= item.maximum
                and (value - item.minimum) % item.step == 0
            )
        elif isinstance(item, TextSetting):
            valid = (
                isinstance(value, str)
                and len(value) <= item.max_length
                and "\n" not in value
                and "\r" not in value
            )
        else:
            valid = False

        if not valid:
            raise ValueError(f"Invalid value for setting: {item.key}")


class PluginHarness(Generic[PluginType]):
    def __init__(
            self,
            plugin_class: type[PluginType],
            *,
            plugin_id: str = "test.plugin",
            settings: dict[str, Any] | None = None,
            storage_backend: HarnessStorageBackend | None = None,
            account: int = 0,
            owner_id: int = 1,
    ):
        if not isinstance(plugin_class, type) or not issubclass(plugin_class, Plugin):
            raise TypeError("plugin_class must inherit from fluxgram.Plugin")

        self.plugin_id = plugin_id
        self.account = account
        self.owner_id = owner_id
        self._owners = {account: owner_id}
        self.generation = 1
        self.logs = []
        self.ui = HarnessUI()
        self._ui_thread = _UIThread()
        self._events = HarnessEvents(self._ui_thread)
        self.client = HarnessClient(self._ui_thread)
        self._ui_services = _UIServices(self.ui)
        self.storage_backend = storage_backend or HarnessStorageBackend()
        self._values = dict(settings or {})
        self._host = _Host(self.generation, self._values)
        self.tasks = HarnessTaskScheduler()
        self._closed = False

        migration_session = self.storage_backend.session(
            plugin_id,
            self._ui_thread.is_ui_thread,
            self._ui_thread.post,
        )
        migration_storage = Storage(migration_session)
        migration_storage._bind_runtime(self._host, self.generation)

        try:
            _migrate_plugin_storage(plugin_class, migration_storage)
        finally:
            migration_storage._close()

        self._storage_session = self.storage_backend.session(
            plugin_id,
            self._ui_thread.is_ui_thread,
            self._ui_thread.post,
        )

        bindings = RuntimeBindings(
            events=self._events,
            client=self.client,
            ui_thread=self._ui_thread,
            ui_services=self._ui_services,
            call_ui=self._ui_thread.call,
            log=lambda current_id, message: self.logs.append((current_id, message)),
            storage=self._storage_session,
            tasks=self.tasks,
        )

        try:
            with runtime_scope(lambda current_id: bindings):
                self.plugin = plugin_class(plugin_id)

            self.settings = HarnessSettings(self, self._values)
            self.plugin._bind_runtime(self._host, self.generation)
            self.run_worker(self.plugin.on_load)
        except BaseException as error:
            self._host.invalidate()

            try:
                if hasattr(self, "plugin"):
                    self.run_worker(self.plugin._unload)
                else:
                    self._storage_session.close()
            except BaseException as cleanup_error:
                error.add_note(f"Plugin cleanup also failed: {cleanup_error!r}")

            raise

    @property
    def active(self) -> bool:
        return not self._closed and self._host.isGenerationActive(self.generation)

    @property
    def events(self) -> HarnessEvents:
        return self._events

    def emit(self, name: str, event: MessageReceivedEvent) -> tuple[Any, ...]:
        return self._events.emit(name, event)

    def emit_event(self, event) -> None:
        context = getattr(event, "context", None)

        if (
                isinstance(context, EventContext)
                and self.owner_for(context.account) != context.owner_id
        ):
            return

        self.run_worker(lambda: self.plugin.events._dispatch(event))

    def owner_for(self, account: int) -> int | None:
        return self._owners.get(account)

    def set_active_account(self, account: int, owner_id: int) -> None:
        if type(account) is not int or account < 0:
            raise ValueError("account must be a non-negative integer")

        if type(owner_id) is not int or owner_id < 0:
            raise ValueError("owner_id must be a non-negative integer")

        self.account = account
        self.owner_id = owner_id
        self._owners[account] = owner_id

    def replace_owner(self, account: int, owner_id: int) -> None:
        if account not in self._owners:
            raise ValueError("account is not registered")

        if type(owner_id) is not int or owner_id < 0:
            raise ValueError("owner_id must be a non-negative integer")

        self._owners[account] = owner_id

        if self.account == account:
            self.owner_id = owner_id

    def dispatch_before_send(self, event: BeforeSendEvent) -> Any:
        if not isinstance(event, BeforeSendEvent):
            raise TypeError("event must be a BeforeSendEvent")

        if not self.active:
            return None

        return self.run_worker(lambda: self.plugin.before_send(event))

    def run_ui(self, callback: Callable[[], Any]) -> Any:
        return self._ui_thread.call(callback)

    def run_worker(self, callback: Callable[[], Any]) -> Any:
        return self._ui_thread.call_worker(callback)

    def drain_ui(self) -> None:
        self._ui_thread.drain()

    def drain_storage(self) -> None:
        self.run_worker(self._storage_session.drain)

    def invalidate_generation(self) -> None:
        self._host.invalidate()
        self.tasks.invalidate()
        self.generation += 1
        self.settings.clear_pending()

    def disable(self) -> None:
        if self._closed:
            return

        if self.active:
            self.invalidate_generation()

        self.run_worker(self.plugin._unload)
        self.drain_ui()
        self._closed = True

    unload = disable

    def assert_bulletin_shown(
            self,
            text: str | None = None,
            *,
            title: str | None = None,
    ) -> BulletinCall:
        for call in reversed(self.ui.bulletins):
            if text is not None and call.text != text:
                continue

            if title is not None and call.title != title:
                continue

            return call

        raise AssertionError("Matching bulletin was not shown")

    def assert_clean(self) -> None:
        failures = []

        if not self._closed:
            failures.append("runtime is active")

        if self._events.active_count:
            failures.append("event subscriptions remain active")

        if self.plugin.events.typed_active_count:
            failures.append("typed event subscriptions remain active")

        if self._ui_thread.pending_count:
            failures.append("UI callbacks remain queued")

        if self.plugin.ui._cleanup_callbacks:
            failures.append("UI cleanup callbacks remain registered")

        if not self._storage_session.closed:
            failures.append("storage session remains active")

        if self.tasks.active_count:
            failures.append("managed tasks remain active")

        if failures:
            raise AssertionError("Plugin runtime is not clean: " + ", ".join(failures))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.disable()

        if exc_type is None:
            self.assert_clean()

        return False
