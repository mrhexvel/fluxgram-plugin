from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import Any, Generic, Protocol, TypeVar

from .models import ChatInfo, MessageInfo


EventType = TypeVar("EventType")
SettingValue = bool | str | int


class LegacySubscription(Protocol):
    def unsubscribe(self) -> None:
        ...


class LegacyEventsPort(Protocol):
    def bind_runtime(self, host: Any, generation: int) -> None:
        ...

    def subscribe(
            self,
            name: str,
            callback: Callable[[Any], Any],
            *,
            thread: str = "ui",
    ) -> LegacySubscription:
        ...

    def close(self) -> None:
        ...


class ExecutionContext(str, Enum):
    PLUGIN = "plugin"


@dataclass(frozen=True)
class EventContext:
    account: int
    owner_id: int
    generation: int

    def __post_init__(self):
        if type(self.account) is not int or self.account < 0:
            raise ValueError("Event account must be a non-negative integer")

        if type(self.owner_id) is not int or self.owner_id < 0:
            raise ValueError("Event owner ID must be a non-negative integer")

        if type(self.generation) is not int or self.generation <= 0:
            raise ValueError("Event generation must be a positive integer")


@dataclass(frozen=True)
class BeforeSendEvent:
    account: int
    text_length: int
    schedule_date: int

    @property
    def scheduled(self) -> bool:
        return self.schedule_date != 0


@dataclass(frozen=True)
class MessageReceivedEvent:
    account: int
    chat: ChatInfo
    message: MessageInfo

    @property
    def dialog_id(self) -> int:
        return self.message.dialog_id

    @property
    def message_id(self) -> int:
        return self.message.id

    @property
    def topic_id(self) -> int:
        return self.message.topic_id

    @property
    def text(self) -> str:
        return self.message.text


@dataclass(frozen=True)
class PluginSettingChangedEvent:
    context: EventContext
    key: str
    value: SettingValue

    def __post_init__(self):
        if not isinstance(self.context, EventContext):
            raise TypeError("Event context must be an EventContext")

        if not isinstance(self.key, str) or not self.key:
            raise ValueError("Setting key must be a non-empty string")

        if type(self.value) not in (bool, str, int):
            raise TypeError("Setting value must be a bool, string or integer")

    @property
    def account(self) -> int:
        return self.context.account

    @property
    def owner_id(self) -> int:
        return self.context.owner_id

    @property
    def generation(self) -> int:
        return self.context.generation


class EventSubscription:
    def __init__(
            self,
            owner: "Events",
            channel: "EventChannel[Any]",
            callback: Callable[[Any], Any],
            execution_context: ExecutionContext,
    ):
        self._owner = owner
        self.channel = channel
        self.callback = callback
        self.execution_context = execution_context
        self._closed = False
        self._lock = Lock()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def unsubscribe(self) -> None:
        with self._lock:
            if self._closed:
                return

            self._closed = True
            self.callback = None

        self._owner._remove(self)


class EventChannel(Generic[EventType]):
    def __init__(
            self,
            owner: "Events",
            name: str,
            event_type: type[EventType],
    ):
        self._owner = owner
        self.name = name
        self.event_type = event_type

    def subscribe(
            self,
            callback: Callable[[EventType], Any],
            *,
            context: ExecutionContext = ExecutionContext.PLUGIN,
    ) -> EventSubscription:
        return self._owner._subscribe(self, callback, context)


class Events:
    def __init__(self, legacy_port: LegacyEventsPort):
        self._legacy_port = legacy_port
        self._legacy_closed = False
        self.plugin_setting_changed = EventChannel(
            self,
            "plugin.setting_changed",
            PluginSettingChangedEvent,
        )
        self._typed_channels = {
            PluginSettingChangedEvent: self.plugin_setting_changed,
        }
        self._typed_subscriptions = []
        self._typed_host = None
        self._typed_generation = None
        self._typed_closed = False
        self._typed_error_handler = lambda name, error: None
        self._typed_lock = Lock()

    def _set_error_handler(
            self,
            callback: Callable[[str, BaseException], Any],
    ) -> None:
        self._typed_error_handler = callback

    def bind_runtime(self, host: Any, generation: int) -> None:
        with self._typed_lock:
            if self._typed_closed or self._typed_host is not None:
                raise RuntimeError("Plugin event bus is inactive")

            self._typed_host = host
            self._typed_generation = generation

        self._legacy_port.bind_runtime(host, generation)

    def subscribe(
            self,
            name: str,
            callback: Callable[[Any], Any],
            *,
            thread: str = "ui",
    ) -> LegacySubscription:
        return self._legacy_port.subscribe(name, callback, thread=thread)

    def _subscribe(
            self,
            channel: EventChannel[EventType],
            callback: Callable[[EventType], Any],
            context: ExecutionContext,
    ) -> EventSubscription:
        if not callable(callback):
            raise TypeError("callback must be callable")

        if context is not ExecutionContext.PLUGIN:
            raise ValueError("Typed events support only ExecutionContext.PLUGIN")

        if self._typed_channels.get(channel.event_type) is not channel:
            raise ValueError("Event channel is not registered by this event bus")

        with self._typed_lock:
            if self._typed_closed:
                raise RuntimeError("Plugin event bus is closed")

            subscription = EventSubscription(self, channel, callback, context)
            self._typed_subscriptions.append(subscription)
            return subscription

    def _remove(self, subscription: EventSubscription) -> None:
        with self._typed_lock:
            if subscription in self._typed_subscriptions:
                self._typed_subscriptions.remove(subscription)

    def _dispatch(self, event: Any) -> None:
        channel = self._typed_channels.get(type(event))

        if channel is None:
            raise TypeError(f"Unsupported typed event: {type(event).__name__}")

        with self._typed_lock:
            if not self._active_locked():
                return

            if event.context.generation != self._typed_generation:
                return

            subscriptions = tuple(self._typed_subscriptions)

        for subscription in subscriptions:
            if subscription.channel is not channel:
                continue

            with subscription._lock:
                if subscription._closed:
                    continue

                callback = subscription.callback

            try:
                callback(event)
            except BaseException as error:
                try:
                    self._typed_error_handler(channel.name, error)
                except BaseException:
                    pass

    def close(self) -> None:
        with self._typed_lock:
            if self._typed_closed:
                subscriptions = ()
            else:
                self._typed_closed = True
                subscriptions = tuple(self._typed_subscriptions)
                self._typed_subscriptions.clear()
                self._typed_host = None
                self._typed_generation = None

        for subscription in subscriptions:
            with subscription._lock:
                subscription._closed = True
                subscription.callback = None

        if not self._legacy_closed:
            self._legacy_port.close()
            self._legacy_closed = True

    @property
    def typed_active_count(self) -> int:
        with self._typed_lock:
            return len(self._typed_subscriptions)

    def _active_locked(self) -> bool:
        return (
            not self._typed_closed
            and self._typed_host is not None
            and self._typed_host.isGenerationActive(self._typed_generation)
        )
