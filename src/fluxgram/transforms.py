from dataclasses import dataclass

from .events import EventContext


@dataclass(frozen=True)
class OutgoingTextTransformRequest:
    """Immutable snapshot of an outgoing plain-text send.

    The request is captured before entity extraction and message splitting. It
    is valid only for its account owner and runtime generation.
    """

    context: EventContext
    dialog_id: int
    topic_id: int
    text: str
    notify: bool
    schedule_date: int
    schedule_repeat_period: int
    pay_stars: int

    def __post_init__(self):
        if not isinstance(self.context, EventContext):
            raise TypeError("Transform context must be an EventContext")

        if type(self.dialog_id) is not int or self.dialog_id == 0:
            raise ValueError("Transform dialog ID must be a non-zero integer")

        if type(self.topic_id) is not int or self.topic_id < 0:
            raise ValueError("Transform topic ID must be a non-negative integer")

        if not isinstance(self.text, str):
            raise TypeError("Transform text must be a string")

        if type(self.notify) is not bool:
            raise TypeError("Transform notify must be a boolean")

        if type(self.schedule_date) is not int or self.schedule_date < 0:
            raise ValueError("Transform schedule date must be a non-negative integer")

        if (
                type(self.schedule_repeat_period) is not int
                or self.schedule_repeat_period < 0
        ):
            raise ValueError(
                "Transform schedule repeat period must be a non-negative integer"
            )

        if type(self.pay_stars) is not int or self.pay_stars < 0:
            raise ValueError("Transform paid amount must be a non-negative integer")

    @property
    def account(self) -> int:
        """Return the captured Telegram account index."""

        return self.context.account

    @property
    def owner_id(self) -> int:
        """Return the captured Telegram account owner ID."""

        return self.context.owner_id

    @property
    def scheduled(self) -> bool:
        """Return whether the send has a nonzero schedule date."""

        return self.schedule_date != 0
