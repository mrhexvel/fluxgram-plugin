class Navigation:
    """Telegram navigation service bound to an active ``UI`` context."""

    def __init__(self, ui):
        self._ui = ui

    def open_chat(
        self,
        *,
        account: int,
        dialog_id: int,
        topic_id: int = 0,
        message_id: int = 0,
    ) -> bool:
        """Open a chat, optional topic, or message on the UI thread.

        Return ``False`` when the target cannot be resolved or presented.
        """

        self._ui._require_ui()

        return self._ui._require_services().open_chat(
            account,
            dialog_id,
            topic_id,
            message_id,
        )
