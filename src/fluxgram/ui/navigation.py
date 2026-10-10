class Navigation:
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
        self._ui._require_ui()

        return self._ui._require_services().open_chat(
            account,
            dialog_id,
            topic_id,
            message_id,
        )
