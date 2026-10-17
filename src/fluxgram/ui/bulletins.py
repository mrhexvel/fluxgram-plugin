from collections.abc import Callable
from typing import Any


class Bulletins:
    """Telegram-themed transient bulletin service bound to a ``UI`` context.

    The presenter is dismissed automatically when the plugin UI closes.
    """

    def __init__(self, ui):
        self._presenter = ui._require_services().create_bulletin_presenter(ui)
        ui._register_cleanup(self._presenter.dismiss)

    def show(
        self,
        title: str,
        text: str,
        *,
        on_click: Callable[[], Any] | None = None,
        duration: int = 4000,
        top: bool = True,
    ) -> bool:
        """Show a bulletin and return whether the host accepted it.

        ``on_click`` runs on the UI thread while the presentation remains
        valid. A false result is a safe no-presentation fallback.
        """

        return self._presenter.simple(
            title, text, on_click=on_click, duration=duration, top=top,
        )
