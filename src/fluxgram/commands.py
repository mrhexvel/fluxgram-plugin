from collections.abc import Callable, Iterable
from dataclasses import dataclass
from re import fullmatch
from threading import Lock

from .transforms import OutgoingTextTransformRequest


@dataclass(frozen=True)
class CommandInvocation:
    """Immutable snapshot passed to a matched command handler."""

    request: OutgoingTextTransformRequest
    name: str
    invoked_as: str
    arguments: str

    def __post_init__(self):
        if not isinstance(self.request, OutgoingTextTransformRequest):
            raise TypeError("Command request must be an OutgoingTextTransformRequest")

        for field, value in (
                ("name", self.name),
                ("invoked_as", self.invoked_as),
                ("arguments", self.arguments),
        ):
            if not isinstance(value, str):
                raise TypeError(f"Command {field} must be a string")

    @property
    def account(self) -> int:
        """Return the Telegram account captured by the send request."""

        return self.request.account

    @property
    def dialog_id(self) -> int:
        """Return the destination dialog ID captured by the send request."""

        return self.request.dialog_id

    @property
    def topic_id(self) -> int:
        """Return the destination topic ID, or zero outside a topic."""

        return self.request.topic_id


CommandHandler = Callable[[CommandInvocation], str | None]


@dataclass(frozen=True)
class _CommandRecord:
    name: str
    aliases: tuple[str, ...]
    handler: CommandHandler


class CommandRegistration:
    """Lifecycle handle for one command and its aliases."""

    def __init__(self, owner: "Commands", record: _CommandRecord):
        self._owner = owner
        self._record = record
        self._active = True

    @property
    def active(self) -> bool:
        """Return whether this registration is still dispatchable."""

        return self._active and self._owner._is_registered(self._record)

    @property
    def name(self) -> str:
        """Return the normalized primary dot-command name."""

        return self._record.name

    @property
    def aliases(self) -> tuple[str, ...]:
        """Return the normalized immutable alias tuple."""

        return self._record.aliases

    def unregister(self) -> None:
        """Remove the command registration; repeated calls have no effect."""

        if not self._active:
            return

        self._owner._unregister(self._record)
        self._active = False

    close = unregister


class Commands:
    """Route exact first-token dot commands for outgoing text transforms.

    Matching is case-insensitive and registrations are automatically removed
    during plugin unload.
    """

    def __init__(self):
        self._lock = Lock()
        self._records: dict[str, _CommandRecord] = {}
        self._closed = False

    def register(
            self,
            name: str,
            handler: CommandHandler,
            *,
            aliases: Iterable[str] = (),
    ) -> CommandRegistration:
        """Register a command handler and optional aliases.

        Names start with ``.`` and contain at most 31 ASCII characters after
        normalization. Conflicting triggers are rejected immediately.
        """

        normalized_name = self._normalize(name)

        if not callable(handler):
            raise TypeError("Command handler must be callable")

        if isinstance(aliases, (str, bytes)):
            raise TypeError("Command aliases must be an iterable of strings")

        normalized_aliases = tuple(self._normalize(alias) for alias in aliases)
        triggers = (normalized_name,) + normalized_aliases

        if len(set(triggers)) != len(triggers):
            raise ValueError("Command name and aliases must be unique")

        record = _CommandRecord(
            name=normalized_name,
            aliases=normalized_aliases,
            handler=handler,
        )

        with self._lock:
            if self._closed:
                raise RuntimeError("Commands are closed")

            conflict = next(
                (trigger for trigger in triggers if trigger in self._records),
                None,
            )

            if conflict is not None:
                raise ValueError(f"Command trigger is already registered: {conflict}")

            for trigger in triggers:
                self._records[trigger] = record

        return CommandRegistration(self, record)

    def command(
            self,
            name: str,
            *,
            aliases: Iterable[str] = (),
    ) -> Callable[[CommandHandler], CommandHandler]:
        """Return a decorator that registers a command handler."""

        def register(handler: CommandHandler) -> CommandHandler:
            self.register(name, handler, aliases=aliases)

            return handler

        return register

    @property
    def active_count(self) -> int:
        """Return the number of active command registrations."""

        with self._lock:
            return len({id(record) for record in self._records.values()})

    def _has_registrations(self) -> bool:
        with self._lock:
            return not self._closed and bool(self._records)

    def _dispatch(self, request: OutgoingTextTransformRequest) -> str | None:
        text = request.text
        separator = next(
            (index for index, character in enumerate(text) if character.isspace()),
            len(text),
        )
        invoked_as = text[:separator]

        if not invoked_as:
            return None

        with self._lock:
            if self._closed:
                return None

            record = self._records.get(invoked_as.lower())

        if record is None:
            return None

        invocation = CommandInvocation(
            request=request,
            name=record.name,
            invoked_as=invoked_as,
            arguments=text[separator:].strip(),
        )

        return record.handler(invocation)

    def _unregister(self, record: _CommandRecord) -> None:
        with self._lock:
            for trigger in (record.name,) + record.aliases:
                if self._records.get(trigger) is record:
                    del self._records[trigger]

    def _is_registered(self, record: _CommandRecord) -> bool:
        with self._lock:
            return not self._closed and self._records.get(record.name) is record

    def _close(self) -> None:
        with self._lock:
            self._closed = True
            self._records.clear()

    @staticmethod
    def _normalize(name: str) -> str:
        if not isinstance(name, str):
            raise TypeError("Command name must be a string")

        if fullmatch(r"\.[A-Za-z][A-Za-z0-9_-]{0,30}", name) is None:
            raise ValueError(
                "Command name must start with a dot and contain 1-31 ASCII characters"
            )

        return name.lower()
