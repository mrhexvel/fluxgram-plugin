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
    context: EventContext | None = None

    def __post_init__(self):
        if self.context is not None:
            if not isinstance(self.context, EventContext):
                raise TypeError("Event context must be an EventContext")

            if self.context.account != self.account:
                raise ValueError("Event account must match its context")

            if not isinstance(self.chat, ChatInfo):
                raise TypeError("Event chat must be a ChatInfo")

            if not isinstance(self.message, MessageInfo):
                raise TypeError("Event message must be a MessageInfo")

            if self.chat.id != self.message.dialog_id:
                raise ValueError("Chat and message dialog IDs must match")

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
class MessageEditedEvent:
    account: int
    chat: ChatInfo
    message: MessageInfo
    edit_date: int
    context: EventContext

    def __post_init__(self):
        if not isinstance(self.context, EventContext):
            raise TypeError("Event context must be an EventContext")

        if self.context.account != self.account:
            raise ValueError("Event account must match its context")

        if not isinstance(self.chat, ChatInfo):
            raise TypeError("Event chat must be a ChatInfo")

        if not isinstance(self.message, MessageInfo):
            raise TypeError("Event message must be a MessageInfo")

        if self.chat.id != self.message.dialog_id:
            raise ValueError("Chat and message dialog IDs must match")

        if type(self.edit_date) is not int or self.edit_date <= 0:
            raise ValueError("Message edit date must be a positive integer")

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
class MessageDeletedEvent:
    context: EventContext
    message_id: int
    channel_id: int | None = None

    def __post_init__(self):
        if not isinstance(self.context, EventContext):
            raise TypeError("Event context must be an EventContext")

        if type(self.message_id) is not int or self.message_id <= 0:
            raise ValueError("Deleted message ID must be a positive integer")

        if self.channel_id is not None:
            if type(self.channel_id) is not int or self.channel_id <= 0:
                raise ValueError("Deleted message channel ID must be positive")

    @property
    def account(self) -> int:
        return self.context.account

    @property
    def owner_id(self) -> int:
        return self.context.owner_id

    @property
    def generation(self) -> int:
        return self.context.generation

    @property
    def dialog_id(self) -> int | None:
        if self.channel_id is None:
            return None

        return -self.channel_id


@dataclass(frozen=True)
class ChatOpenedEvent:
    context: EventContext
    presentation_id: int
    dialog_id: int
    topic_id: int = 0

    def __post_init__(self):
        if not isinstance(self.context, EventContext):
            raise TypeError("Event context must be an EventContext")

        if type(self.presentation_id) is not int or self.presentation_id <= 0:
            raise ValueError("Chat presentation ID must be a positive integer")

        if type(self.dialog_id) is not int or self.dialog_id == 0:
            raise ValueError("Chat dialog ID must be a non-zero integer")

        if type(self.topic_id) is not int or self.topic_id < 0:
            raise ValueError("Chat topic ID must be a non-negative integer")

    @property
    def account(self) -> int:
        return self.context.account

    @property
    def owner_id(self) -> int:
        return self.context.owner_id

    @property
    def generation(self) -> int:
        return self.context.generation


@dataclass(frozen=True)
class ChatClosedEvent:
    context: EventContext
    presentation_id: int
    dialog_id: int
    topic_id: int = 0

    def __post_init__(self):
        if not isinstance(self.context, EventContext):
            raise TypeError("Event context must be an EventContext")

        if type(self.presentation_id) is not int or self.presentation_id <= 0:
            raise ValueError("Chat presentation ID must be a positive integer")

        if type(self.dialog_id) is not int or self.dialog_id == 0:
            raise ValueError("Chat dialog ID must be a non-zero integer")

        if type(self.topic_id) is not int or self.topic_id < 0:
            raise ValueError("Chat topic ID must be a non-negative integer")

    @property
    def account(self) -> int:
        return self.context.account

    @property
    def owner_id(self) -> int:
        return self.context.owner_id

    @property
    def generation(self) -> int:
        return self.context.generation


@dataclass(frozen=True)
class ApplicationForegroundedEvent:
    context: EventContext

    def __post_init__(self):
        if not isinstance(self.context, EventContext):
            raise TypeError("Event context must be an EventContext")

    @property
    def account(self) -> int:
        return self.context.account

    @property
    def owner_id(self) -> int:
        return self.context.owner_id

    @property
    def generation(self) -> int:
        return self.context.generation


@dataclass(frozen=True)
class ApplicationBackgroundedEvent:
    context: EventContext

    def __post_init__(self):
        if not isinstance(self.context, EventContext):
            raise TypeError("Event context must be an EventContext")

    @property
    def account(self) -> int:
        return self.context.account

    @property
    def owner_id(self) -> int:
        return self.context.owner_id

    @property
    def generation(self) -> int:
        return self.context.generation


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
        self.message_received = EventChannel(
            self,
            "message.received",
            MessageReceivedEvent,
        )
        self.message_edited = EventChannel(
            self,
            "message.edited",
            MessageEditedEvent,
        )
        self.message_deleted = EventChannel(
            self,
            "message.deleted",
            MessageDeletedEvent,
        )
        self.chat_opened = EventChannel(
            self,
            "chat.opened",
            ChatOpenedEvent,
        )
        self.chat_closed = EventChannel(
            self,
            "chat.closed",
            ChatClosedEvent,
        )
        self.application_foregrounded = EventChannel(
            self,
            "application.foregrounded",
            ApplicationForegroundedEvent,
        )
        self.application_backgrounded = EventChannel(
            self,
            "application.backgrounded",
            ApplicationBackgroundedEvent,
        )
        self._typed_channels = {
            PluginSettingChangedEvent: self.plugin_setting_changed,
            MessageReceivedEvent: self.message_received,
            MessageEditedEvent: self.message_edited,
            MessageDeletedEvent: self.message_deleted,
            ChatOpenedEvent: self.chat_opened,
            ChatClosedEvent: self.chat_closed,
            ApplicationForegroundedEvent: self.application_foregrounded,
            ApplicationBackgroundedEvent: self.application_backgrounded,
        }
        self._typed_subscriptions = []
        self._typed_host = None
        self._typed_generation = None
        self._typed_closed = False
        self._message_delivery_active = False
        self._message_edited_delivery_active = False
        self._message_deleted_delivery_active = False
        self._chat_opened_delivery_active = False
        self._chat_closed_delivery_active = False
        self._application_foregrounded_delivery_active = False
        self._application_backgrounded_delivery_active = False
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

            if self._has_message_subscriptions_locked():
                host.setMessageReceivedSubscriptionActive(generation, True)
                self._message_delivery_active = True

            if self._has_message_edited_subscriptions_locked():
                host.setMessageEditedSubscriptionActive(generation, True)
                self._message_edited_delivery_active = True

            if self._has_message_deleted_subscriptions_locked():
                host.setMessageDeletedSubscriptionActive(generation, True)
                self._message_deleted_delivery_active = True

            if self._has_chat_opened_subscriptions_locked():
                host.setChatOpenedSubscriptionActive(generation, True)
                self._chat_opened_delivery_active = True

            if self._has_chat_closed_subscriptions_locked():
                host.setChatClosedSubscriptionActive(generation, True)
                self._chat_closed_delivery_active = True

            if self._has_application_foregrounded_subscriptions_locked():
                host.setApplicationForegroundedSubscriptionActive(generation, True)
                self._application_foregrounded_delivery_active = True

            if self._has_application_backgrounded_subscriptions_locked():
                host.setApplicationBackgroundedSubscriptionActive(generation, True)
                self._application_backgrounded_delivery_active = True

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

            if (
                    channel is self.message_received
                    and self._typed_host is not None
                    and not self._message_delivery_active
            ):
                try:
                    self._typed_host.setMessageReceivedSubscriptionActive(
                        self._typed_generation,
                        True,
                    )
                    self._message_delivery_active = True
                except BaseException:
                    self._typed_subscriptions.remove(subscription)
                    raise

            if (
                    channel is self.message_edited
                    and self._typed_host is not None
                    and not self._message_edited_delivery_active
            ):
                try:
                    self._typed_host.setMessageEditedSubscriptionActive(
                        self._typed_generation,
                        True,
                    )
                    self._message_edited_delivery_active = True
                except BaseException:
                    self._typed_subscriptions.remove(subscription)
                    raise

            if (
                    channel is self.message_deleted
                    and self._typed_host is not None
                    and not self._message_deleted_delivery_active
            ):
                try:
                    self._typed_host.setMessageDeletedSubscriptionActive(
                        self._typed_generation,
                        True,
                    )
                    self._message_deleted_delivery_active = True
                except BaseException:
                    self._typed_subscriptions.remove(subscription)
                    raise

            if (
                    channel is self.chat_opened
                    and self._typed_host is not None
                    and not self._chat_opened_delivery_active
            ):
                try:
                    self._typed_host.setChatOpenedSubscriptionActive(
                        self._typed_generation,
                        True,
                    )
                    self._chat_opened_delivery_active = True
                except BaseException:
                    self._typed_subscriptions.remove(subscription)
                    raise

            if (
                    channel is self.chat_closed
                    and self._typed_host is not None
                    and not self._chat_closed_delivery_active
            ):
                try:
                    self._typed_host.setChatClosedSubscriptionActive(
                        self._typed_generation,
                        True,
                    )
                    self._chat_closed_delivery_active = True
                except BaseException:
                    self._typed_subscriptions.remove(subscription)
                    raise

            if (
                    channel is self.application_foregrounded
                    and self._typed_host is not None
                    and not self._application_foregrounded_delivery_active
            ):
                try:
                    self._typed_host.setApplicationForegroundedSubscriptionActive(
                        self._typed_generation,
                        True,
                    )
                    self._application_foregrounded_delivery_active = True
                except BaseException:
                    self._typed_subscriptions.remove(subscription)
                    raise

            if (
                    channel is self.application_backgrounded
                    and self._typed_host is not None
                    and not self._application_backgrounded_delivery_active
            ):
                try:
                    self._typed_host.setApplicationBackgroundedSubscriptionActive(
                        self._typed_generation,
                        True,
                    )
                    self._application_backgrounded_delivery_active = True
                except BaseException:
                    self._typed_subscriptions.remove(subscription)
                    raise

            return subscription

    def _remove(self, subscription: EventSubscription) -> None:
        with self._typed_lock:
            if subscription in self._typed_subscriptions:
                self._typed_subscriptions.remove(subscription)

            if (
                    subscription.channel is self.message_received
                    and self._message_delivery_active
                    and not self._has_message_subscriptions_locked()
            ):
                self._typed_host.setMessageReceivedSubscriptionActive(
                    self._typed_generation,
                    False,
                )
                self._message_delivery_active = False

            if (
                    subscription.channel is self.message_edited
                    and self._message_edited_delivery_active
                    and not self._has_message_edited_subscriptions_locked()
            ):
                self._typed_host.setMessageEditedSubscriptionActive(
                    self._typed_generation,
                    False,
                )
                self._message_edited_delivery_active = False

            if (
                    subscription.channel is self.message_deleted
                    and self._message_deleted_delivery_active
                    and not self._has_message_deleted_subscriptions_locked()
            ):
                self._typed_host.setMessageDeletedSubscriptionActive(
                    self._typed_generation,
                    False,
                )
                self._message_deleted_delivery_active = False

            if (
                    subscription.channel is self.chat_opened
                    and self._chat_opened_delivery_active
                    and not self._has_chat_opened_subscriptions_locked()
            ):
                self._typed_host.setChatOpenedSubscriptionActive(
                    self._typed_generation,
                    False,
                )
                self._chat_opened_delivery_active = False

            if (
                    subscription.channel is self.chat_closed
                    and self._chat_closed_delivery_active
                    and not self._has_chat_closed_subscriptions_locked()
            ):
                self._typed_host.setChatClosedSubscriptionActive(
                    self._typed_generation,
                    False,
                )
                self._chat_closed_delivery_active = False

            if (
                    subscription.channel is self.application_foregrounded
                    and self._application_foregrounded_delivery_active
                    and not self._has_application_foregrounded_subscriptions_locked()
            ):
                self._typed_host.setApplicationForegroundedSubscriptionActive(
                    self._typed_generation,
                    False,
                )
                self._application_foregrounded_delivery_active = False

            if (
                    subscription.channel is self.application_backgrounded
                    and self._application_backgrounded_delivery_active
                    and not self._has_application_backgrounded_subscriptions_locked()
            ):
                self._typed_host.setApplicationBackgroundedSubscriptionActive(
                    self._typed_generation,
                    False,
                )
                self._application_backgrounded_delivery_active = False

    def _dispatch(self, event: Any) -> None:
        channel = self._typed_channels.get(type(event))

        if channel is None:
            raise TypeError(f"Unsupported typed event: {type(event).__name__}")

        if not isinstance(event.context, EventContext):
            raise TypeError("Typed events require an EventContext")

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

                if self._message_delivery_active:
                    self._typed_host.setMessageReceivedSubscriptionActive(
                        self._typed_generation,
                        False,
                    )
                    self._message_delivery_active = False

                if self._message_edited_delivery_active:
                    self._typed_host.setMessageEditedSubscriptionActive(
                        self._typed_generation,
                        False,
                    )
                    self._message_edited_delivery_active = False

                if self._message_deleted_delivery_active:
                    self._typed_host.setMessageDeletedSubscriptionActive(
                        self._typed_generation,
                        False,
                    )
                    self._message_deleted_delivery_active = False

                if self._chat_opened_delivery_active:
                    self._typed_host.setChatOpenedSubscriptionActive(
                        self._typed_generation,
                        False,
                    )
                    self._chat_opened_delivery_active = False

                if self._chat_closed_delivery_active:
                    self._typed_host.setChatClosedSubscriptionActive(
                        self._typed_generation,
                        False,
                    )
                    self._chat_closed_delivery_active = False

                if self._application_foregrounded_delivery_active:
                    self._typed_host.setApplicationForegroundedSubscriptionActive(
                        self._typed_generation,
                        False,
                    )
                    self._application_foregrounded_delivery_active = False

                if self._application_backgrounded_delivery_active:
                    self._typed_host.setApplicationBackgroundedSubscriptionActive(
                        self._typed_generation,
                        False,
                    )
                    self._application_backgrounded_delivery_active = False

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

    def _has_message_subscriptions_locked(self) -> bool:
        return any(
            subscription.channel is self.message_received
            and not subscription._closed
            for subscription in self._typed_subscriptions
        )

    def _has_message_edited_subscriptions_locked(self) -> bool:
        return any(
            subscription.channel is self.message_edited
            and not subscription._closed
            for subscription in self._typed_subscriptions
        )

    def _has_message_deleted_subscriptions_locked(self) -> bool:
        return any(
            subscription.channel is self.message_deleted
            and not subscription._closed
            for subscription in self._typed_subscriptions
        )

    def _has_chat_opened_subscriptions_locked(self) -> bool:
        return any(
            subscription.channel is self.chat_opened
            and not subscription._closed
            for subscription in self._typed_subscriptions
        )

    def _has_chat_closed_subscriptions_locked(self) -> bool:
        return any(
            subscription.channel is self.chat_closed
            and not subscription._closed
            for subscription in self._typed_subscriptions
        )

    def _has_application_foregrounded_subscriptions_locked(self) -> bool:
        return any(
            subscription.channel is self.application_foregrounded
            and not subscription._closed
            for subscription in self._typed_subscriptions
        )

    def _has_application_backgrounded_subscriptions_locked(self) -> bool:
        return any(
            subscription.channel is self.application_backgrounded
            and not subscription._closed
            for subscription in self._typed_subscriptions
        )
