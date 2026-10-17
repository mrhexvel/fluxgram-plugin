import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import tomllib
import zipfile
import zlib

from . import JAVA_API_MINOR, JAVA_API_VERSION, SDK_MINOR, SDK_VERSION
from .device import DeviceError, install_archive, stream_logs


MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_UNPACKED_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_ENTRIES = 512
DEX_MAGICS = {b"dex\n035\0", b"dex\n037\0", b"dex\n038\0", b"dex\n039\0"}
PATH_PART = re.compile(r"[A-Za-z0-9_.-]+")
PLUGIN_ID = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+")
PLUGIN_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
PYTHON_ENTRYPOINT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*:PluginEntry")
JAVA_ENTRYPOINT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_$]*)+")
HASH = re.compile(r"[a-f0-9]{64}")
FIXED_TIME = (2020, 1, 1, 0, 0, 0)
HOST_FINGERPRINT = "f2ff60d61f95bb4c1587fecde7d32978e38bbd8c4e3aec2b8d0a9bcdd7e98e6b"
TEMPLATES = ("python", "java", "hybrid")


class DevkitError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise DevkitError(message)


def strict_object(pairs):
    value = {}

    for key, item in pairs:
        require(key not in value, f"Duplicate JSON field: {key}")
        value[key] = item

    return value


def read_json(data):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DevkitError("Invalid manifest JSON") from error


def validate_string(value, name, limit):
    require(isinstance(value, str), f"{name} must be a string")
    require(value.strip() and len(value) <= limit, f"Invalid {name}")

    for character in value:
        code = ord(character)
        require(character in "\n\t" or code >= 32 and code != 127, f"Invalid control character in {name}")
        require(not 0x202A <= code <= 0x202E and not 0x2066 <= code <= 0x2069,
                f"Bidirectional controls are not allowed in {name}")

    return value


def validate_integer(value, name):
    require(type(value) is int and 0 <= value <= 2_147_483_647,
            f"{name} must be a non-negative 32-bit integer")
    return value


def validate_path(name, directory=False):
    require(isinstance(name, str) and 0 < len(name) <= 240, "Invalid ZIP path length")
    require("\\" not in name and not name.startswith("/"), f"Unsafe ZIP path: {name}")
    path = name[:-1] if directory and name.endswith("/") else name
    parts = path.split("/")
    require(path and len(parts) <= 16, f"Unsafe ZIP path: {name}")

    for part in parts:
        require(PATH_PART.fullmatch(part) is not None and part not in {".", ".."}
                and not part.endswith("."), f"Unsafe ZIP path: {name}")

    require(parts[0].lower() not in {"fluxgram", "fluxgram.py", ".fluxgram-source.fgplugin"},
            f"Reserved package path: {name}")
    return path


def validate_manifest(manifest):
    require(isinstance(manifest, dict), "Manifest must be an object")
    base = {
        "schema_version", "id", "name", "version", "description", "author", "access",
        "declared_capabilities",
    }
    python_fields = {"sdk_version", "min_sdk_minor", "entrypoint"}
    optional = python_fields | {"icon", "java_module"}
    require(set(manifest).issubset(base | optional), "Unknown manifest field")
    require(base.issubset(manifest), "Missing manifest field")
    schema = validate_integer(manifest["schema_version"], "schema_version")
    require(schema in {2, 3, 4, 5}, "Only manifest schema 2, 3, 4 and 5 are supported")
    present_python_fields = set(manifest) & python_fields

    if schema in {2, 3}:
        require(present_python_fields == python_fields, "Missing Python module field")
    else:
        require(not present_python_fields or present_python_fields == python_fields,
                "Incomplete Python module")

    if present_python_fields:
        require(validate_integer(manifest["sdk_version"], "sdk_version") == SDK_VERSION,
                f"SDK major must be {SDK_VERSION}")
        require(validate_integer(manifest["min_sdk_minor"], "min_sdk_minor") <= SDK_MINOR,
                f"Plugin requires a newer SDK than {SDK_VERSION}.{SDK_MINOR}")
        entrypoint = validate_string(manifest["entrypoint"], "entrypoint", 128)
        require(PYTHON_ENTRYPOINT.fullmatch(entrypoint) is not None
                and entrypoint != "fluxgram:PluginEntry", "Invalid Python entrypoint")

    plugin_id = validate_string(manifest["id"], "id", 100)
    require(PLUGIN_ID.fullmatch(plugin_id) is not None and not plugin_id.startswith("fluxgram."),
            "Invalid or reserved plugin id")
    validate_string(manifest["name"], "name", 80)
    version = validate_string(manifest["version"], "version", 64)
    require(PLUGIN_VERSION.fullmatch(version) is not None, "Version must have the form major.minor.patch")
    validate_string(manifest["description"], "description", 2000)
    validate_string(manifest["author"], "author", 100)
    require(manifest["access"] == "full", "Only full access is supported")
    capabilities = manifest["declared_capabilities"]
    require(isinstance(capabilities, list) and len(capabilities) <= 32,
            "declared_capabilities must be an array of at most 32 strings")
    seen = set()

    for capability in capabilities:
        value = validate_string(capability, "declared capability", 160)
        require(value not in seen, "Duplicate declared capability")
        seen.add(value)

    if "icon" in manifest:
        icon = validate_path(validate_string(manifest["icon"], "icon", 240))
        require(icon.lower().endswith((".png", ".webp")), "Icon must be PNG or WebP")

    java = manifest.get("java_module")
    require(schema >= 3 or java is None, "java_module requires schema 3 or newer")
    require(schema != 3 or java is not None, "java_module is required for schema 3")
    require(schema not in {4, 5} or present_python_fields or java is not None,
            "Manifest must declare a module")
    require(schema != 5 or java is not None, "Schema 5 requires a Java module")

    if java is not None:
        require(isinstance(java, dict), "java_module must be an object")
        fields = {"path", "entrypoint", "sha256", "api_version", "min_api_minor"}
        if schema == 5:
            fields.add("host_fingerprint")
        require(set(java) == fields, "Incomplete or unknown java_module field")
        dex_path = validate_path(validate_string(java["path"], "Java module path", 240))
        require(dex_path.endswith(".dex"), "Java module must be a DEX file")
        java_entrypoint = validate_string(java["entrypoint"], "Java entrypoint", 240)
        require(JAVA_ENTRYPOINT.fullmatch(java_entrypoint) is not None, "Invalid Java entrypoint")
        require(HASH.fullmatch(validate_string(java["sha256"], "DEX SHA-256", 64)) is not None,
                "Invalid DEX SHA-256")
        require(validate_integer(java["api_version"], "Java API version") == JAVA_API_VERSION,
                f"Java API major must be {JAVA_API_VERSION}")
        require(validate_integer(java["min_api_minor"], "Java API minor") <= JAVA_API_MINOR,
                f"Plugin requires a newer Java API than {JAVA_API_VERSION}.{JAVA_API_MINOR}")

        if schema == 5:
            fingerprint = validate_string(java["host_fingerprint"], "Host fingerprint", 64)
            require(HASH.fullmatch(fingerprint) is not None, "Invalid host fingerprint")
            require(fingerprint == HOST_FINGERPRINT, "Plugin requires an incompatible host")

    return manifest


def validate_dex(data, java):
    require(len(data) >= 112 and data[:8] in DEX_MAGICS, "Unsupported or truncated DEX")
    require(hashlib.sha256(data).hexdigest() == java["sha256"], "DEX SHA-256 mismatch")
    file_size, header_size, endian = struct.unpack_from("<III", data, 32)
    require(file_size == len(data) and header_size == 112 and endian == 0x12345678,
            "Invalid DEX header")
    strings, strings_offset = struct.unpack_from("<II", data, 56)
    types, types_offset = struct.unpack_from("<II", data, 64)
    classes, classes_offset = struct.unpack_from("<II", data, 96)

    def table(offset, count, width):
        require(offset >= 112 and count > 0 and offset + count * width <= len(data),
                "Invalid DEX table")

    table(strings_offset, strings, 4)
    table(types_offset, types, 4)
    table(classes_offset, classes, 32)
    expected = "L" + java["entrypoint"].replace(".", "/") + ";"
    found = False

    for index in range(classes):
        type_index = struct.unpack_from("<I", data, classes_offset + index * 32)[0]
        require(type_index < types, "Invalid DEX class type")
        string_index = struct.unpack_from("<I", data, types_offset + type_index * 4)[0]
        require(string_index < strings, "Invalid DEX type descriptor")
        offset = struct.unpack_from("<I", data, strings_offset + string_index * 4)[0]
        require(112 <= offset < len(data), "Invalid DEX string offset")

        for _ in range(5):
            require(offset < len(data), "Invalid DEX string length")
            value = data[offset]
            offset += 1

            if value & 128 == 0:
                break
        else:
            raise DevkitError("Invalid DEX string length")

        end = data.find(b"\0", offset, min(len(data), offset + 1025))
        require(end >= 0, "Unterminated or oversized DEX descriptor")

        try:
            descriptor = data[offset:end].decode("utf-8")
        except UnicodeDecodeError as error:
            raise DevkitError("Invalid DEX class descriptor") from error

        require(descriptor.startswith("L") and descriptor.endswith(";"),
                "Invalid DEX class descriptor")
        forbidden = (
            "Lorg/telegram/", "Lorg/fluxgram/", "Landroid/", "Ljava/", "Ljavax/",
            "Ldalvik/", "Lcom/chaquo/",
        )
        require(not descriptor.startswith(forbidden), "DEX bundles host/platform classes")
        found |= descriptor == expected

    require(found, "Java entrypoint is not defined in DEX")


def read_number(data, offset, length, byteorder):
    require(0 <= offset and offset + length <= len(data), "Truncated image data")
    return int.from_bytes(data[offset:offset + length], byteorder)


def validate_icon(data, path):
    require(20 <= len(data) <= 512 * 1024, "Invalid icon size")
    width = 0
    height = 0

    if path.lower().endswith(".png"):
        require(data[:8] == b"\x89PNG\r\n\x1a\n", "Invalid PNG icon")
        offset = 8
        end = False
        pixels = False

        while offset + 12 <= len(data):
            length = read_number(data, offset, 4, "big")
            require(length <= len(data) - offset - 12, "Truncated PNG icon")
            kind = data[offset + 4:offset + 8]
            checksum = read_number(data, offset + 8 + length, 4, "big")
            require(zlib.crc32(data[offset + 4:offset + 8 + length]) == checksum,
                    "Invalid PNG checksum")
            require(kind not in {b"acTL", b"fcTL", b"fdAT"}, "Animated icons are not supported")

            if offset == 8:
                require(kind == b"IHDR" and length == 13, "Invalid PNG header")
                width = read_number(data, offset + 8, 4, "big")
                height = read_number(data, offset + 12, 4, "big")
            else:
                require(kind != b"IHDR", "Duplicate PNG header")

            pixels |= kind == b"IDAT"
            offset += length + 12

            if kind == b"IEND":
                require(length == 0 and offset == len(data), "Invalid PNG end")
                end = True
                break

        require(end and pixels, "Incomplete PNG icon")
    else:
        require(data[:4] == b"RIFF" and data[8:12] == b"WEBP", "Invalid WebP icon")
        require(read_number(data, 4, 4, "little") == len(data) - 8, "Invalid WebP length")
        offset = 12
        pixels = False

        while offset + 8 <= len(data):
            kind = data[offset:offset + 4]
            length = read_number(data, offset + 4, 4, "little")
            require(length <= len(data) - offset - 8, "Truncated WebP icon")
            start = offset + 8
            require(kind not in {b"ANIM", b"ANMF"}, "Animated icons are not supported")

            if kind == b"VP8X":
                require(length == 10 and data[start] & 2 == 0, "Animated or invalid WebP icon")
                check_icon_dimensions(read_number(data, start + 4, 3, "little") + 1,
                                      read_number(data, start + 7, 3, "little") + 1)
            elif kind == b"VP8 ":
                require(not pixels and length >= 10 and data[start + 3:start + 6] == b"\x9d\x01\x2a",
                        "Invalid WebP frame")
                width = read_number(data, start + 6, 2, "little") & 0x3FFF
                height = read_number(data, start + 8, 2, "little") & 0x3FFF
                pixels = True
            elif kind == b"VP8L":
                require(not pixels and length >= 5 and data[start] == 0x2F, "Invalid WebP frame")
                bits = read_number(data, start + 1, 4, "little")
                width = (bits & 0x3FFF) + 1
                height = ((bits >> 14) & 0x3FFF) + 1
                pixels = True

            offset += 8 + length + (length & 1)

        require(pixels and offset == len(data), "Incomplete WebP icon")

    check_icon_dimensions(width, height)


def check_icon_dimensions(width, height):
    require(0 < width <= 1024 and 0 < height <= 1024,
            "Icon dimensions must be between 1 and 1024 pixels")


def inspect_archive(path):
    path = Path(path)
    require(path.is_file(), f"Package does not exist: {path}")
    require(path.stat().st_size <= MAX_ARCHIVE_BYTES, "Archive exceeds 8 MiB")
    spellings = {}
    kinds = {}
    contents = {}
    total = 0

    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as error:
        raise DevkitError("Invalid ZIP archive") from error

    with archive:
        entries = archive.infolist()
        require(len(entries) <= MAX_ENTRIES, "Too many ZIP entries")

        for entry in entries:
            require(entry.flag_bits & 1 == 0, "Encrypted ZIP entries are not supported")
            require(entry.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED},
                    "Unsupported ZIP compression")
            directory = entry.is_dir()
            name = validate_path(entry.filename, directory)
            require(name not in kinds, f"Duplicate ZIP path: {name}")
            kinds[name] = directory
            prefix = ""

            for part in name.split("/"):
                prefix = part if not prefix else prefix + "/" + part
                key = prefix.lower()
                require(key not in spellings or spellings[key] == prefix, "Case-conflicting ZIP path")
                spellings[key] = prefix

            mode = entry.external_attr >> 16

            if mode:
                require(not stat.S_ISLNK(mode), "Symbolic links are not supported")
                require(directory or stat.S_ISREG(mode), "Special files are not supported")

            limit = MAX_MANIFEST_BYTES if name == "manifest.json" else MAX_FILE_BYTES
            require(0 <= entry.file_size <= limit, f"Oversized file: {name}")
            require(not directory or entry.file_size == 0, "Directory contains file data")
            data = archive.read(entry)
            require(len(data) == entry.file_size, f"Corrupt ZIP entry: {name}")
            total += len(data)
            require(total <= MAX_UNPACKED_BYTES, "Unpacked size exceeds 32 MiB")

            if not directory:
                contents[name] = data

        for name in kinds:
            parts = name.split("/")

            for index in range(1, len(parts)):
                require(kinds.get("/".join(parts[:index])) is not False, "File used as a directory")

    require("manifest.json" in contents, "Missing root manifest.json")
    manifest = validate_manifest(read_json(contents["manifest.json"]))
    if "entrypoint" in manifest:
        python_file = manifest["entrypoint"].split(":", 1)[0] + ".py"
        require(python_file in contents, "Missing Python entrypoint file")

    if manifest.get("java_module") is not None:
        java = manifest["java_module"]
        require(java["path"] in contents, "Missing Java module")
        validate_dex(contents[java["path"]], java)

    if "icon" in manifest:
        require(manifest["icon"] in contents, "Missing plugin icon")
        validate_icon(contents[manifest["icon"]], manifest["icon"])

    return {
        "id": manifest["id"],
        "version": manifest["version"],
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "files": len(contents),
        "bytes": total,
    }


def load_project(project_dir):
    project_dir = Path(project_dir).resolve()
    config_path = project_dir / "fluxgram-plugin.toml"
    require(config_path.is_file(), f"Missing project file: {config_path}")

    try:
        config = tomllib.loads(config_path.read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise DevkitError("Invalid fluxgram-plugin.toml") from error

    require(set(config).issubset({"plugin", "python", "java", "assets"}), "Unknown project section")
    require("plugin" in config, "Project requires a plugin section")
    plugin = config["plugin"]
    python = config.get("python")
    java = config.get("java")
    require(isinstance(plugin, dict), "Invalid plugin section")
    require(python is not None or java is not None, "Project requires a python or java section")

    if python is not None:
        require(isinstance(python, dict), "Invalid python section")

    plugin_fields = {
        "id", "name", "version", "description", "author", "min_sdk_minor",
        "declared_capabilities", "icon",
    }
    require(set(plugin).issubset(plugin_fields), "Unknown plugin project field")
    required_plugin = plugin_fields - {"icon", "min_sdk_minor"}
    require(required_plugin.issubset(plugin), "Missing plugin project field")
    require(("min_sdk_minor" in plugin) == (python is not None),
            "min_sdk_minor is required only for Python projects")

    if python is not None:
        require(set(python) == {"root", "entrypoint", "files"}, "Invalid python project fields")

    assets = config.get("assets", {"root": "assets", "files": []})
    require(isinstance(assets, dict) and set(assets) == {"root", "files"}, "Invalid assets section")

    if java is not None:
        require(isinstance(java, dict), "Invalid java section")
        java_fields = {"sources", "entrypoint", "min_api_minor", "host_stubs"}
        require(set(java).issubset(java_fields)
                and {"sources", "entrypoint", "min_api_minor"}.issubset(java),
                "Invalid java project fields")
        require(type(java.get("host_stubs", False)) is bool,
                "java.host_stubs must be a boolean")

    manifest = {
        "schema_version": 5 if java is not None and java.get("host_stubs", False) else 4,
        "id": plugin["id"],
        "name": plugin["name"],
        "version": plugin["version"],
        "description": plugin["description"],
        "author": plugin["author"],
        "access": "full",
        "declared_capabilities": plugin["declared_capabilities"],
    }

    if python is not None:
        manifest.update({
            "sdk_version": SDK_VERSION,
            "min_sdk_minor": plugin["min_sdk_minor"],
            "entrypoint": python["entrypoint"],
        })

    if "icon" in plugin:
        manifest["icon"] = plugin["icon"]

    if java is not None:
        manifest["java_module"] = {
            "path": "java/module.dex",
            "entrypoint": java["entrypoint"],
            "sha256": "0" * 64,
            "api_version": JAVA_API_VERSION,
            "min_api_minor": java["min_api_minor"],
        }

        if java.get("host_stubs", False):
            manifest["java_module"]["host_fingerprint"] = HOST_FINGERPRINT

    validate_manifest(manifest)
    return project_dir, config, manifest


def list_files(project_dir, root_name, names):
    require(isinstance(root_name, str), "Project root must be a string")
    require(isinstance(names, list) and all(isinstance(name, str) for name in names),
            "Project files must be an array of strings")
    root = (project_dir / root_name).resolve()
    require(root == project_dir or project_dir in root.parents, "Project root escapes the project")
    result = []

    for name in names:
        destination = validate_path(name)
        source = (root / name).resolve()
        require(root in source.parents and source.is_file(), f"Missing project file: {source}")
        require(source.stat().st_size <= MAX_FILE_BYTES, f"Project file exceeds 8 MiB: {source}")
        result.append((destination, source.read_bytes()))

    return result


def find_java(java_home):
    homes = []

    if java_home:
        homes.append(Path(java_home))

    if os.environ.get("JAVA_HOME"):
        homes.append(Path(os.environ["JAVA_HOME"]))

    for home in homes:
        executable = home / "bin" / ("javac.exe" if os.name == "nt" else "javac")

        if executable.is_file():
            return executable

    executable = shutil.which("javac")
    require(executable is not None, "javac was not found; pass --java-home or set JAVA_HOME")
    return Path(executable)


def find_android(android_sdk, build_tools):
    candidates = []

    if android_sdk:
        candidates.append(Path(android_sdk))

    for key in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        if os.environ.get(key):
            candidates.append(Path(os.environ[key]))

    candidates.extend([
        Path.home() / "Library/Android/sdk",
        Path.home() / "Android/Sdk",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Android/Sdk",
    ])
    sdk = next((item for item in candidates if (item / "platforms/android-36/android.jar").is_file()), None)
    require(sdk is not None, "Android SDK platform 36 was not found; pass --android-sdk")
    android_jar = sdk / "platforms/android-36/android.jar"

    if build_tools:
        tools = sdk / "build-tools" / build_tools
        require(tools.is_dir(), f"Android build-tools {build_tools} were not found")
    else:
        roots = [item for item in (sdk / "build-tools").iterdir()
                 if item.is_dir() and all(part.isdigit() for part in item.name.split("."))
                 and item.name.split(".", 1)[0] in {"35", "36"}]
        require(roots, "Android build-tools 35 or 36 were not found")
        tools = sorted(roots, key=lambda item: tuple(int(part) for part in item.name.split(".")))[-1]

    d8 = tools / ("d8.bat" if os.name == "nt" else "d8")
    require(d8.is_file(), f"D8 was not found in {tools}")
    return sdk, android_jar, tools, d8


def run(command, label):
    process = subprocess.run([str(item) for item in command], text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    if process.returncode != 0:
        raise DevkitError(f"{label} failed:\n{process.stdout.strip()}")


def api_artifact():
    root = Path(__file__).with_name("artifacts")
    artifact = root / f"fluxgram-java-api-{JAVA_API_VERSION}.{JAVA_API_MINOR}.jar"
    metadata_path = root / "api.json"
    require(artifact.is_file() and metadata_path.is_file(), "Bundled Java API artifact is missing")

    try:
        metadata = json.loads(metadata_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DevkitError("Invalid Java API metadata") from error

    fields = {
        "api_version", "api_minor", "min_sdk", "compile_sdk", "sha256",
        "sources_jar_sha256", "source_sha256", "host_validator_sha256", "source_commit",
    }
    require(isinstance(metadata, dict) and set(metadata) == fields, "Invalid Java API metadata")
    require(metadata["api_version"] == JAVA_API_VERSION and metadata["api_minor"] == JAVA_API_MINOR,
            "Java API metadata version mismatch")
    require(metadata["min_sdk"] == 24 and metadata["compile_sdk"] == 36,
            "Java API Android metadata mismatch")
    require(HASH.fullmatch(metadata["sha256"]) is not None
            and HASH.fullmatch(metadata["source_sha256"]) is not None
            and HASH.fullmatch(metadata["host_validator_sha256"]) is not None
            and isinstance(metadata["source_commit"], str) and metadata["source_commit"],
            "Invalid Java API metadata values")
    require(hashlib.sha256(artifact.read_bytes()).hexdigest() == metadata["sha256"],
            "Java API artifact digest mismatch")
    return artifact, metadata


def api_sources_artifact():
    root = Path(__file__).with_name("artifacts")
    artifact = root / f"fluxgram-java-api-{JAVA_API_VERSION}.{JAVA_API_MINOR}-sources.jar"
    _, metadata = api_artifact()
    require(artifact.is_file(), "Bundled Java API sources artifact is missing")
    require(HASH.fullmatch(metadata["sources_jar_sha256"]) is not None,
            "Invalid Java API sources metadata")
    require(hashlib.sha256(artifact.read_bytes()).hexdigest() == metadata["sources_jar_sha256"],
            "Java API sources artifact digest mismatch")
    return artifact


def host_stubs_artifact():
    root = Path(__file__).with_name("artifacts")
    artifact = root / "fluxgram-host-stubs.jar"
    metadata_path = root / "host-stubs.json"
    require(artifact.is_file() and metadata_path.is_file(), "Bundled host stubs are missing")

    try:
        metadata = json.loads(metadata_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DevkitError("Invalid host stubs metadata") from error

    fields = {
        "host_fingerprint", "compile_sdk", "stable_api", "jar_sha256", "source_sha256",
        "contract",
    }
    require(isinstance(metadata, dict) and set(metadata) == fields,
            "Invalid host stubs metadata")
    require(metadata["host_fingerprint"] == HOST_FINGERPRINT,
            "Host stubs fingerprint mismatch")
    require(metadata["compile_sdk"] == 36 and metadata["stable_api"] == "2.0",
            "Host stubs build metadata mismatch")
    require(HASH.fullmatch(metadata["jar_sha256"]) is not None
            and HASH.fullmatch(metadata["source_sha256"]) is not None,
            "Invalid host stubs metadata values")
    require(hashlib.sha256(artifact.read_bytes()).hexdigest() == metadata["jar_sha256"],
            "Host stubs artifact digest mismatch")
    source = (root / "host-stubs-src/org/telegram/messenger/plugins/"
              "ComposerInputRendererExtensions.java")
    require(source.is_file()
            and hashlib.sha256(source.read_bytes()).hexdigest() == metadata["source_sha256"],
            "Host stubs source digest mismatch")
    contract = metadata["contract"]
    require(contract == {
        "id": "ui.chat.composer.input.native_draw",
        "version": 4,
        "mode": "replacement",
        "thread": "main",
        "abi": [
            "register(JavaPluginContext,Factory)->JavaPluginUiResource",
            "Factory.create(Surface)->Renderer throws Exception",
            "Surface.getAccount()->int",
            "Surface.getOwnerId()->long",
            "Surface.getDialogId()->long",
            "Surface.getGeometry()->ComposerGeometry",
            "Surface.isActive()->boolean",
            "Surface.requestFrame()->void",
            "Surface.selectTextClusters(ComposerTextClusterSelection)->void",
            "Renderer.getCapabilities()->int",
            "Renderer.onInput(ComposerInput)->void throws Exception",
            "Renderer.onTextLayoutChanged(ComposerTextLayoutChange)->void throws Exception",
            "Renderer.onSend(ComposerSendEvent)->void throws Exception",
            "Renderer.onFrame(long)->boolean throws Exception",
            "Renderer.draw(Frame)->Result throws Exception",
            "Renderer.drawExpanded(ExpandedFrame)->void throws Exception",
            "Renderer.close()->void throws Exception",
            "Frame.getCanvas()->Canvas",
            "Frame.getGeometry()->ComposerGeometry",
            "Frame.getWidth()->int",
            "Frame.getHeight()->int",
            "Frame.getTextClusterCapture()->ComposerTextClusterCapture",
            "Frame.getRemovedTextClusterCapture()->ComposerTextClusterCapture",
            "Frame.drawNative()->void",
            "Frame.drawNative(ComposerTextClusterPresentation,ComposerTextClusterPresentation)->void",
            "ExpandedFrame.getCanvas()->Canvas",
            "ExpandedFrame.getGeometry()->ComposerGeometry",
            "ExpandedFrame.getBounds()->ComposerExpandedBounds",
            "Result=COMMIT|FALLBACK",
        ],
    }, "Invalid host stubs contract")
    return artifact, metadata


def project_schema_artifact():
    artifact = Path(__file__).with_name("artifacts") / "fluxgram-plugin.schema.json"
    require(artifact.is_file(), "Bundled project schema is missing")

    try:
        schema = json.loads(artifact.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DevkitError("Invalid bundled project schema") from error

    require(isinstance(schema, dict)
            and schema.get("$id") == "https://fluxgram.org/schemas/fluxgram-plugin.schema.json",
            "Invalid bundled project schema")
    return artifact


def write_artifact(source, destination):
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_bytes(source.read_bytes())
    temporary.replace(destination)


def write_generated(data, destination):
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(destination)


def prepare_ide(project_dir):
    project_dir, config, _ = load_project(project_dir)
    destination = project_dir / ".fluxgram" / "ide"
    destination.mkdir(parents=True, exist_ok=True)
    expected = {"fluxgram-plugin.schema.json"}
    write_artifact(project_schema_artifact(), destination / "fluxgram-plugin.schema.json")
    repository = destination / "repository"
    shutil.rmtree(repository, ignore_errors=True)

    if "java" in config:
        api, _ = api_artifact()
        sources = api_sources_artifact()
        version = f"{JAVA_API_VERSION}.{JAVA_API_MINOR}"
        module = repository / "org" / "fluxgram" / "fluxgram-java-api" / version
        module.mkdir(parents=True)
        write_artifact(api, module / f"fluxgram-java-api-{version}.jar")
        write_artifact(sources, module / f"fluxgram-java-api-{version}-sources.jar")
        pom = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<project xmlns="http://maven.apache.org/POM/4.0.0" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 '
            'https://maven.apache.org/xsd/maven-4.0.0.xsd">\n'
            '  <modelVersion>4.0.0</modelVersion>\n'
            '  <groupId>org.fluxgram</groupId>\n'
            '  <artifactId>fluxgram-java-api</artifactId>\n'
            f'  <version>{version}</version>\n'
            '</project>\n'
        ).encode("utf-8")
        write_generated(pom, module / f"fluxgram-java-api-{version}.pom")

        if config["java"].get("host_stubs", False):
            stubs, _ = host_stubs_artifact()
            write_artifact(stubs, destination / "fluxgram-host-stubs.jar")
            expected.add("fluxgram-host-stubs.jar")

    for name in ("fluxgram-java-api.jar", "fluxgram-host-stubs.jar"):
        path = destination / name

        if name not in expected:
            path.unlink(missing_ok=True)

    return destination


def zip_bytes(files):
    output = io.BytesIO()

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(files):
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)

    output.seek(0)
    return output.read()


def compile_java(project_dir, java_config, java_home, android_sdk, build_tools):
    javac = find_java(java_home)
    sdk, android_jar, tools, d8 = find_android(android_sdk, build_tools)
    source_roots = java_config["sources"]
    require(isinstance(source_roots, list) and source_roots, "java.sources must not be empty")
    sources = []

    for root_name in source_roots:
        require(isinstance(root_name, str), "Java source root must be a string")
        root = (project_dir / root_name).resolve()
        require(project_dir in root.parents and root.is_dir(), f"Missing Java source root: {root}")
        sources.extend(sorted(root.rglob("*.java")))

    require(sources, "No Java sources were found")
    api_jar, _ = api_artifact()
    classpath = [android_jar, api_jar]

    if java_config.get("host_stubs", False):
        stubs_jar, _ = host_stubs_artifact()
        classpath.append(stubs_jar)

    with tempfile.TemporaryDirectory(prefix="fluxgram-plugin-") as temporary:
        temporary = Path(temporary)
        classes = temporary / "classes"
        classes.mkdir()
        run([javac, "--release", "8", "-cp", os.pathsep.join(str(item) for item in classpath),
             "-d", classes, *sources], "javac")
        class_files = [(str(path.relative_to(classes)).replace(os.sep, "/"), path.read_bytes())
                       for path in classes.rglob("*.class")]
        require(class_files, "javac produced no classes")
        input_jar = temporary / "plugin.jar"
        input_jar.write_bytes(zip_bytes(class_files))
        dex_dir = temporary / "dex"
        dex_dir.mkdir()
        d8_command = [d8, "--min-api", "24", "--lib", android_jar]

        for item in classpath[1:]:
            d8_command.extend(["--classpath", item])

        run([*d8_command, "--output", dex_dir, input_jar], "D8")
        dex_files = sorted(dex_dir.glob("*.dex"))
        require([item.name for item in dex_files] == ["classes.dex"], "D8 produced unsupported multidex output")
        data = dex_files[0].read_bytes()
        require(data[:8] in DEX_MAGICS, "D8 produced a DEX version unsupported by FluxGram")
        return data, javac, sdk, tools


def build_project(project_dir, output=None, java_home=None, android_sdk=None, build_tools=None):
    project_dir, config, manifest = load_project(project_dir)
    files = []

    if "python" in config:
        files.extend(list_files(
            project_dir, config["python"]["root"], config["python"]["files"]))

    assets = config.get("assets", {"root": "assets", "files": []})
    files.extend(list_files(project_dir, assets["root"], assets["files"]))
    java = config.get("java")

    if java is not None:
        dex, _, _, _ = compile_java(project_dir, java, java_home, android_sdk, build_tools)
        manifest["java_module"] = {
            "path": "java/module.dex",
            "entrypoint": java["entrypoint"],
            "sha256": hashlib.sha256(dex).hexdigest(),
            "api_version": JAVA_API_VERSION,
            "min_api_minor": java["min_api_minor"],
        }

        if java.get("host_stubs", False):
            manifest["java_module"]["host_fingerprint"] = HOST_FINGERPRINT
        validate_manifest(manifest)
        files.append(("java/module.dex", dex))

    manifest_data = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    files.append(("manifest.json", manifest_data))
    names = [name for name, _ in files]
    require(len(names) == len(set(names)), "Project maps multiple files to the same package path")
    package = zip_bytes(files)
    require(len(package) <= MAX_ARCHIVE_BYTES, "Archive exceeds 8 MiB")

    if output is None:
        output = project_dir / "dist" / f"{manifest['id']}-{manifest['version']}.fgplugin"
    else:
        output = Path(output).resolve()

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(package)
    inspect_archive(output)
    return output


def initialize_project(destination, template="hybrid"):
    destination = Path(destination).resolve()
    require(not destination.exists(), f"Destination already exists: {destination}")
    require(template in TEMPLATES, f"Unknown project template: {template}")
    source = Path(__file__).with_name("templates") / template
    require(source.is_dir(), "Bundled project template is missing")
    shutil.copytree(source, destination)

    try:
        gitignore = destination / "gitignore"

        if gitignore.is_file():
            gitignore.replace(destination / ".gitignore")

        prepare_ide(destination)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise

    return destination


def doctor(java_home=None, android_sdk=None, build_tools=None):
    javac = find_java(java_home)
    sdk, android_jar, tools, d8 = find_android(android_sdk, build_tools)
    artifact, _ = api_artifact()
    stubs, metadata = host_stubs_artifact()
    print(f"javac: {javac}")
    print(f"android.jar: {android_jar}")
    print(f"build-tools: {tools}")
    print(f"d8: {d8}")
    print(f"java-api: {JAVA_API_VERSION}.{JAVA_API_MINOR} {artifact}")
    print(f"host-stubs: {metadata['host_fingerprint']} {stubs}")
    print(f"android-sdk: {sdk}")


def parser():
    value = argparse.ArgumentParser(prog="fluxgram-plugin")
    commands = value.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("destination")
    init.add_argument("--template", choices=TEMPLATES, default="hybrid")
    new = commands.add_parser("new")
    new.add_argument("destination")
    new.add_argument("--template", choices=TEMPLATES, default="hybrid")
    prepare = commands.add_parser("prepare-ide")
    prepare.add_argument("project", nargs="?", default=".")
    build = commands.add_parser("build")
    build.add_argument("project", nargs="?", default=".")
    build.add_argument("--output")
    validate = commands.add_parser("validate")
    validate.add_argument("archive")
    doctor_command = commands.add_parser("doctor")
    install = commands.add_parser("install")
    install.add_argument("archive")
    install.add_argument("--enable", action="store_true")
    reload_command = commands.add_parser("reload")
    reload_command.add_argument("project")
    reload_command.add_argument("--output")
    logs = commands.add_parser("logs")
    logs.add_argument("--plugin", required=True)

    device_commands = (install, reload_command, logs)

    for command in device_commands:
        command.add_argument("--device", required=True)
        command.add_argument("--package", default="org.fluxgram.messenger.beta")
        command.add_argument("--adb", default="adb")

    for command in (build, reload_command, doctor_command):
        command.add_argument("--java-home")
        command.add_argument("--android-sdk")
        command.add_argument("--build-tools")

    return value


def main(argv=None):
    arguments = parser().parse_args(argv)

    try:
        if arguments.command in {"init", "new"}:
            print(initialize_project(arguments.destination, arguments.template))
        elif arguments.command == "prepare-ide":
            print(prepare_ide(arguments.project))
        elif arguments.command == "build":
            print(build_project(arguments.project, arguments.output, arguments.java_home,
                                arguments.android_sdk, arguments.build_tools))
        elif arguments.command == "validate":
            result = inspect_archive(arguments.archive)
            print(f"{result['id']} {result['version']} {result['sha256']}")
        elif arguments.command == "doctor":
            doctor(arguments.java_home, arguments.android_sdk, arguments.build_tools)
        elif arguments.command == "install":
            result = install_archive(arguments.archive, arguments.device, arguments.package,
                                     arguments.adb, "install", arguments.enable)
            print(json.dumps(result, ensure_ascii=False))
        elif arguments.command == "reload":
            archive = build_project(arguments.project, arguments.output, arguments.java_home,
                                    arguments.android_sdk, arguments.build_tools)
            result = install_archive(archive, arguments.device, arguments.package,
                                     arguments.adb, "reload", True)
            print(json.dumps(result, ensure_ascii=False))
        else:
            stream_logs(arguments.device, arguments.package, arguments.adb, arguments.plugin)
    except (DevkitError, DeviceError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
