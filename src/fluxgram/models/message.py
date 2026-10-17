from dataclasses import dataclass


@dataclass(frozen=True)
class MessageInfo:
    """Immutable message metadata snapshot detached from Telegram models.

    ``topic_id`` is zero outside a forum topic. ``sender_id`` is the signed
    Telegram peer ID returned by ``getFromChatId`` and can identify a user,
    chat, or channel; it is ``None`` when the sender cannot be resolved.
    """

    id: int
    dialog_id: int
    topic_id: int

    sender_id: int | None

    text: str
    outgoing: bool
    mentioned: bool
    is_reply: bool
    silent: bool
