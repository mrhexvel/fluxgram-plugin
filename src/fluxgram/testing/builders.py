from ..events import (
    BeforeSendEvent,
    ChatClosedEvent,
    ChatOpenedEvent,
    EventContext,
    MessageDeletedEvent,
    MessageEditedEvent,
    MessageReceivedEvent,
    PluginSettingChangedEvent,
)
from ..models import (
    ChatInfo,
    ChatNotifications,
    MessageInfo,
    NotificationOverride,
)


def notification_override(
        mode: str = "inherit",
        *,
        mute_until: int | None = None,
) -> NotificationOverride:
    return NotificationOverride(mode=mode, mute_until=mute_until)


def chat_notifications(
        *,
        chat_override: NotificationOverride | None = None,
        topic_override: NotificationOverride | None = None,
        effective_muted: bool | None = False,
) -> ChatNotifications:
    return ChatNotifications(
        chat_override=chat_override or notification_override(),
        topic_override=topic_override,
        effective_muted=effective_muted,
    )


def chat(
        dialog_id: int = 1,
        *,
        title: str | None = "Test chat",
        kind: str = "private",
        is_forum: bool | None = False,
        archived: bool | None = False,
        notifications: ChatNotifications | None = None,
) -> ChatInfo:
    return ChatInfo(
        id=dialog_id,
        title=title,
        kind=kind,
        is_forum=is_forum,
        archived=archived,
        notifications=notifications or chat_notifications(),
    )


def message(
        message_id: int = 1,
        *,
        dialog_id: int = 1,
        topic_id: int = 0,
        sender_id: int | None = 2,
        text: str = "Test message",
        outgoing: bool = False,
        mentioned: bool = False,
        is_reply: bool = False,
        silent: bool = False,
) -> MessageInfo:
    return MessageInfo(
        id=message_id,
        dialog_id=dialog_id,
        topic_id=topic_id,
        sender_id=sender_id,
        text=text,
        outgoing=outgoing,
        mentioned=mentioned,
        is_reply=is_reply,
        silent=silent,
    )


def message_received(
        *,
        account: int = 0,
        owner_id: int = 1,
        generation: int = 1,
        context: EventContext | None = None,
        chat_info: ChatInfo | None = None,
        message_info: MessageInfo | None = None,
) -> MessageReceivedEvent:
    current_message = message_info or message()
    current_chat = chat_info or chat(dialog_id=current_message.dialog_id)

    if current_chat.id != current_message.dialog_id:
        raise ValueError("Chat and message dialog IDs must match")

    return MessageReceivedEvent(
        account=account,
        chat=current_chat,
        message=current_message,
        context=context or event_context(
            account=account,
            owner_id=owner_id,
            generation=generation,
        ),
    )


def message_edited(
        *,
        account: int = 0,
        owner_id: int = 1,
        generation: int = 1,
        edit_date: int = 1,
        context: EventContext | None = None,
        chat_info: ChatInfo | None = None,
        message_info: MessageInfo | None = None,
) -> MessageEditedEvent:
    current_message = message_info or message()
    current_chat = chat_info or chat(dialog_id=current_message.dialog_id)

    if current_chat.id != current_message.dialog_id:
        raise ValueError("Chat and message dialog IDs must match")

    return MessageEditedEvent(
        account=account,
        chat=current_chat,
        message=current_message,
        edit_date=edit_date,
        context=context or event_context(
            account=account,
            owner_id=owner_id,
            generation=generation,
        ),
    )


def message_deleted(
        message_id: int = 1,
        *,
        account: int = 0,
        owner_id: int = 1,
        generation: int = 1,
        channel_id: int | None = None,
        context: EventContext | None = None,
) -> MessageDeletedEvent:
    return MessageDeletedEvent(
        context=context or event_context(
            account=account,
            owner_id=owner_id,
            generation=generation,
        ),
        message_id=message_id,
        channel_id=channel_id,
    )


def chat_opened(
        dialog_id: int = 1,
        *,
        presentation_id: int = 1,
        topic_id: int = 0,
        account: int = 0,
        owner_id: int = 1,
        generation: int = 1,
        context: EventContext | None = None,
) -> ChatOpenedEvent:
    return ChatOpenedEvent(
        context=context or event_context(
            account=account,
            owner_id=owner_id,
            generation=generation,
        ),
        presentation_id=presentation_id,
        dialog_id=dialog_id,
        topic_id=topic_id,
    )


def chat_closed(
        dialog_id: int = 1,
        *,
        presentation_id: int = 1,
        topic_id: int = 0,
        account: int = 0,
        owner_id: int = 1,
        generation: int = 1,
        context: EventContext | None = None,
) -> ChatClosedEvent:
    return ChatClosedEvent(
        context=context or event_context(
            account=account,
            owner_id=owner_id,
            generation=generation,
        ),
        presentation_id=presentation_id,
        dialog_id=dialog_id,
        topic_id=topic_id,
    )


def before_send(
        *,
        account: int = 0,
        text_length: int = 0,
        schedule_date: int = 0,
) -> BeforeSendEvent:
    return BeforeSendEvent(
        account=account,
        text_length=text_length,
        schedule_date=schedule_date,
    )


def event_context(
        *,
        account: int = 0,
        owner_id: int = 1,
        generation: int = 1,
) -> EventContext:
    return EventContext(
        account=account,
        owner_id=owner_id,
        generation=generation,
    )


def plugin_setting_changed(
        key: str,
        value: bool | str | int,
        *,
        context: EventContext | None = None,
) -> PluginSettingChangedEvent:
    return PluginSettingChangedEvent(
        context=context or event_context(),
        key=key,
        value=value,
    )
