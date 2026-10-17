import re
from dataclasses import dataclass
from typing import Callable


_SETTING_ICONS = frozenset((
    "adjustments",
    "auto_delete",
    "blur",
    "clear",
    "colors",
    "copy",
    "curve",
    "customize",
    "delete",
    "edit",
    "emoji",
    "favorite",
    "file",
    "flip",
    "link",
    "list",
    "message",
    "notifications",
    "open",
    "particles",
    "reset",
    "retry",
    "select",
    "send",
    "settings",
    "speed",
    "translate",
    "zoom_in",
))


def _validate_common(item):
    if not isinstance(item.key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", item.key):
        raise ValueError("Invalid setting key")

    if not isinstance(item.title, str) or not item.title.strip() or len(item.title) > 120:
        raise ValueError("Invalid setting title")

    if not isinstance(item.description, str) or len(item.description) > 1000:
        raise ValueError("Invalid setting description")

    if not isinstance(item.label, str) or len(item.label) > 120:
        raise ValueError("Invalid setting label")

    if item.on_changed is not None and not callable(item.on_changed):
        raise TypeError("Setting on_changed must be callable")

    if not isinstance(item.icon, str) or item.icon and item.icon not in _SETTING_ICONS:
        raise ValueError("Invalid setting icon")


@dataclass(frozen=True)
class Switch:
    """Immutable declaration of a boolean plugin setting.

    ``on_changed(plugin, value)`` runs on the plugin worker and replaces, rather
    than supplements, ``Plugin.on_setting_changed`` for this field.
    """

    key: str
    title: str
    default: bool = False
    description: str = ""
    label: str = ""
    on_changed: Callable | None = None
    icon: str = ""

    def __post_init__(self):
        _validate_common(self)

        if type(self.default) is not bool:
            raise TypeError("Switch default must be a boolean")


@dataclass(frozen=True)
class Select:
    """Immutable declaration of a choice setting with 1 to 32 options.

    ``on_changed(plugin, value)`` runs on the plugin worker and replaces, rather
    than supplements, ``Plugin.on_setting_changed`` for this field.
    """

    key: str
    title: str
    options: tuple[tuple[str, str], ...]
    default: str
    description: str = ""
    label: str = ""
    on_changed: Callable | None = None
    icon: str = ""

    def __post_init__(self):
        _validate_common(self)
        raw_options = tuple(self.options)

        if any(not isinstance(option, (tuple, list)) for option in raw_options):
            raise ValueError("Each option must be a pair")

        options = tuple(tuple(option) for option in raw_options)

        if not 1 <= len(options) <= 32:
            raise ValueError("Select must have 1 to 32 options")

        for option in options:
            if len(option) != 2 or any(not isinstance(value, str) or not value.strip() for value in option):
                raise ValueError("Each option must contain a value and a title")

            if len(option[0]) > 64 or len(option[1]) > 120:
                raise ValueError("Select option is too long")

        keys = [option[0] for option in options]

        if len(set(keys)) != len(keys) or self.default not in keys:
            raise ValueError("Duplicate option or invalid default")

        object.__setattr__(self, "options", options)


@dataclass(frozen=True)
class Slider:
    """Immutable declaration of a bounded integer slider setting.

    ``on_changed(plugin, value)`` runs on the plugin worker and replaces, rather
    than supplements, ``Plugin.on_setting_changed`` for this field.
    """

    key: str
    title: str
    minimum: int
    maximum: int
    default: int
    step: int = 1
    unit: str = ""
    description: str = ""
    label: str = ""
    on_changed: Callable | None = None
    icon: str = ""

    def __post_init__(self):
        _validate_common(self)

        if any(type(value) is not int for value in (self.minimum, self.maximum, self.default, self.step)):
            raise TypeError("Slider values must be integers")

        if not -1000000 <= self.minimum < self.maximum <= 1000000 or self.step <= 0:
            raise ValueError("Invalid slider range")

        if (self.maximum - self.minimum) % self.step or (self.maximum - self.minimum) // self.step > 1000:
            raise ValueError("Slider range must contain at most 1000 whole steps")

        if not self.minimum <= self.default <= self.maximum or (self.default - self.minimum) % self.step:
            raise ValueError("Invalid slider default")

        if not isinstance(self.unit, str) or len(self.unit) > 20:
            raise ValueError("Invalid slider unit")


@dataclass(frozen=True)
class Text:
    """Immutable declaration of a single-line text setting.

    ``on_changed(plugin, value)`` runs on the plugin worker and replaces, rather
    than supplements, ``Plugin.on_setting_changed`` for this field.
    """

    key: str
    title: str
    default: str = ""
    placeholder: str = ""
    max_length: int = 256
    description: str = ""
    label: str = ""
    on_changed: Callable | None = None
    icon: str = ""

    def __post_init__(self):
        _validate_common(self)

        if type(self.max_length) is not int or not 1 <= self.max_length <= 4096:
            raise ValueError("Text max_length must be between 1 and 4096")

        if not isinstance(self.default, str) or len(self.default) > self.max_length:
            raise ValueError("Invalid text default")

        if "\n" in self.default or "\r" in self.default:
            raise ValueError("Text only supports a single line")

        if not isinstance(self.placeholder, str) or len(self.placeholder) > 120:
            raise ValueError("Invalid text placeholder")


SwitchSetting = Switch
SelectSetting = Select
SliderSetting = Slider
TextSetting = Text


class Settings:
    """Read-only access to the active plugin's declared settings."""

    def __init__(self, schema):
        schema = tuple(schema)

        if len(schema) > 64 or any(not isinstance(item, (Switch, Select, Slider, Text)) for item in schema):
            raise ValueError("Invalid settings schema")

        self._schema = {item.key: item for item in schema}

        if len(self._schema) != len(schema):
            raise ValueError("Duplicate setting key")

        self._host = None
        self._generation = None

    def _bind_runtime(self, host, generation):
        self._host = host
        self._generation = generation

    def get(self, key):
        """Return the validated current value for a declared setting.

        Invalid persisted values fall back to the declaration default. Access
        outside an active plugin generation raises ``RuntimeError``.
        """

        item = self._schema[key]

        if self._host is None or not self._host.isGenerationActive(self._generation):
            raise RuntimeError("Plugin settings are inactive")

        if isinstance(item, Switch):
            return bool(self._host.readBooleanSetting(item.key, item.default))

        if isinstance(item, Select):
            value = str(self._host.readStringSetting(item.key, item.default))
            return value if value in dict(item.options) else item.default

        if isinstance(item, Text):
            value = str(self._host.readStringSetting(item.key, item.default))
            return value if len(value) <= item.max_length and "\n" not in value and "\r" not in value else item.default

        value = int(self._host.readIntSetting(item.key, item.default))

        if not item.minimum <= value <= item.maximum or (value - item.minimum) % item.step:
            return item.default

        return value

    def _dispatch_changed(self, plugin, key, value):
        item = self._schema[key]

        if self._host is None or not self._host.isGenerationActive(self._generation):
            return

        if item.on_changed is not None:
            item.on_changed(plugin, value)
        else:
            plugin.on_setting_changed(key, value)

    def _describe(self):
        result = []

        for item in self._schema.values():
            data = {"key": item.key, "title": item.title, "default": item.default,
                    "description": item.description, "label": item.label.strip()}

            if item.icon:
                data["icon"] = item.icon

            if isinstance(item, Select):
                data.update(type="select", options=item.options)
            elif isinstance(item, Slider):
                data.update(type="slider", minimum=item.minimum, maximum=item.maximum,
                            step=item.step, unit=item.unit)
            elif isinstance(item, Text):
                data.update(type="text", placeholder=item.placeholder, max_length=item.max_length)
            else:
                data["type"] = "switch"

            result.append(data)

        return result
