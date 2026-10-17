from dataclasses import dataclass

from .notifications import ChatNotifications


@dataclass(frozen=True)
class ChatInfo:
    """Immutable chat metadata snapshot.

    ``kind`` is ``unknown``, ``secret``, ``private``, ``group``, or
    ``channel``. The signed ``id`` uses Telegram dialog identity conventions.
    Nullable fields are unknown when the client cannot safely resolve them;
    the object is not a live Telegram model.
    """

    id: int
    title: str | None


    kind: str

    is_forum: bool | None
    archived: bool | None
    notifications: ChatNotifications
