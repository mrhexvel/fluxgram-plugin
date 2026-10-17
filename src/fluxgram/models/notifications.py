from dataclasses import dataclass


@dataclass(frozen=True)
class NotificationOverride:
    """Immutable snapshot of one chat or topic notification override.

    ``mode`` is ``inherit``, ``enabled``, ``muted``, ``until``, or ``unknown``.
    ``mute_until`` is present only when the mode carries a known deadline.
    """

    mode: str
    mute_until: int | None = None


@dataclass(frozen=True)
class ChatNotifications:
    """Immutable snapshot of chat and optional topic notification state.

    ``effective_muted`` is ``None`` when the effective state is unavailable.
    """

    chat_override: NotificationOverride
    topic_override: NotificationOverride | None

    effective_muted: bool | None
