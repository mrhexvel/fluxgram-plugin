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
    StorageQueueFullError,
    StorageQuotaExceededError,
    StorageResult,
    StorageScope,
    StorageStatistics,
    StorageThreadError,
    StorageTransaction,
    StorageTransactionError,
    StorageTask,
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
API_MINOR_VERSION = 1

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
    "StorageResult",
    "StorageTask",
    "StorageError",
    "StorageUnavailableError",
    "StorageInactiveError",
    "StorageAccountUnavailableError",
    "StorageOwnerChangedError",
    "StorageQueueFullError",
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
