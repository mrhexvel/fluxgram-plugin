from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from .models import ChatInfo


if TYPE_CHECKING:
    from .storage import StoragePort
    from .tasks import TasksPort


class Subscription(Protocol):
    def unsubscribe(self) -> None:
        ...


class Events(Protocol):
    def bind_runtime(self, host: Any, generation: int) -> None:
        ...

    def subscribe(
        self,
        name: str,
        callback: Callable[[Any], Any],
        *,
        thread: str = "ui",
    ) -> Subscription:
        ...

    def close(self) -> None:
        ...


class Chats(Protocol):
    def get(
        self,
        *,
        account: int,
        dialog_id: int,
        topic_id: int = 0,
    ) -> ChatInfo:
        ...


class Client(Protocol):
    chats: Chats

    def _bind_runtime(self, host: Any, generation: int) -> None:
        ...

    def _close(self) -> None:
        ...


class UIThread(Protocol):
    def is_ui_thread(self) -> bool:
        ...

    def post(self, callback: Callable[[], Any]) -> None:
        ...


class BulletinPresenter(Protocol):
    def dismiss(self) -> None:
        ...

    def simple(
        self,
        title: str,
        text: str,
        *,
        on_click: Callable[[], Any] | None = None,
        duration: int = 4000,
        top: bool = True,
    ) -> bool:
        ...


class NotificationsService(Protocol):
    def show_message(
        self,
        event: Any,
        *,
        respect_mute: bool = True,
        show_in_current_chat: bool = False,
        duration: int = 5000,
    ) -> bool:
        ...


class UIServices(Protocol):
    def create_bulletin_presenter(self, ui: Any) -> BulletinPresenter:
        ...

    def create_notifications(self, ui: Any) -> NotificationsService:
        ...

    def open_chat(
        self,
        account: int,
        dialog_id: int,
        topic_id: int,
        message_id: int,
    ) -> bool:
        ...


@dataclass(frozen=True)
class RuntimeBindings:
    events: Events
    client: Client
    ui_thread: UIThread
    ui_services: UIServices
    call_ui: Callable[[Callable[[], Any]], Any]
    log: Callable[[str, str], None]
    storage: "StoragePort | None" = None
    tasks: "TasksPort | None" = None


class RuntimeFactory(Protocol):
    def __call__(self, plugin_id: str) -> RuntimeBindings:
        ...


_factory: ContextVar[RuntimeFactory | None] = ContextVar(
    "fluxgram_runtime_factory",
    default=None,
)


def create_runtime(plugin_id: str) -> RuntimeBindings:
    factory = _factory.get()

    if factory is None:
        raise RuntimeError("Plugin instances can only be created by FluxGram")

    return factory(plugin_id)


@contextmanager
def runtime_scope(factory: RuntimeFactory) -> Iterator[None]:
    token = _factory.set(factory)

    try:
        yield
    finally:
        _factory.reset(token)
