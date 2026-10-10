from ..events import MessageReceivedEvent


class Notifications:
    def __init__(self, ui):
        self._ui = ui
        self._delegate = ui._require_services().create_notifications(ui)

    def show_message(
        self,
        event: MessageReceivedEvent,
        *,
        respect_mute: bool = True,
        show_in_current_chat: bool = False,
        duration: int = 5000,
    ) -> bool:
        self._ui._require_ui()

        return self._delegate.show_message(
            event,
            respect_mute=respect_mute,
            show_in_current_chat=show_in_current_chat,
            duration=duration,
        )
