from .plugin import Plugin
from .settings import SwitchSetting, SelectSetting, SliderSetting, TextSetting
from .events import BeforeSendEvent, MessageReceivedEvent
from .storage import (
    Storage,
    StorageAccountUnavailableError,
    StorageError,
    StorageInactiveError,
    StorageLimits,
    StorageMigration,
    StorageMigrationError,
    StorageOwnerChangedError,
    StorageQuotaExceededError,
    StorageScope,
    StorageStatistics,
    StorageThreadError,
    StorageTransaction,
    StorageTransactionError,
    StorageTypeError,
    StorageUnavailableError,
)
from .models import (
    ChatInfo,
    ChatNotifications,
    MessageInfo,
    NotificationOverride,
)

API_VERSION = 2
API_MINOR_VERSION = 0

__all__ = [
    "Plugin",
    "SwitchSetting",
    "SelectSetting",
    "SliderSetting",
    "TextSetting",
    "BeforeSendEvent",
    "MessageReceivedEvent",
    "Storage",
    "StorageScope",
    "StorageTransaction",
    "StorageMigration",
    "StorageLimits",
    "StorageStatistics",
    "StorageError",
    "StorageUnavailableError",
    "StorageInactiveError",
    "StorageAccountUnavailableError",
    "StorageOwnerChangedError",
    "StorageThreadError",
    "StorageTypeError",
    "StorageQuotaExceededError",
    "StorageTransactionError",
    "StorageMigrationError",
    "API_VERSION",
    "API_MINOR_VERSION",
    "ChatInfo",
    "ChatNotifications",
    "MessageInfo",
    "NotificationOverride",
]
