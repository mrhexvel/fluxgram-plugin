import hashlib
import json
from pathlib import Path
import re

from .process import ProcessRunner


MAX_RESPONSE_BYTES = 64 * 1024
PID = re.compile(r"[1-9][0-9]*")


class DeviceError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise DeviceError(message)


def output_text(result):
    data = result.stdout

    if isinstance(data, str):
        return data

    return data.decode("utf-8", "replace")


def run_checked(runner, argv, label, input_data=None):
    result = runner.run(argv, input_data=input_data)

    if result.returncode != 0:
        message = output_text(result).strip()
        stderr = result.stderr

        if not message and stderr:
            message = stderr if isinstance(stderr, str) else stderr.decode("utf-8", "replace")

        raise DeviceError(f"{label} failed: {message.strip()}")

    return result


def online_devices(adb, runner=None):
    runner = runner or ProcessRunner()
    result = run_checked(runner, [adb, "devices"], "adb devices")
    devices = []

    for line in output_text(result).splitlines()[1:]:
        fields = line.split()

        if len(fields) == 2 and fields[1] == "device":
            devices.append(fields[0])

    return devices


def select_device(adb, serial=None, runner=None):
    devices = online_devices(adb, runner)

    if serial is not None:
        require(serial in devices, f"ADB device is not online: {serial}")
        return serial

    require(len(devices) == 1, "Exactly one online ADB device is required")
    return devices[0]


def verify_run_as(adb, serial, package, runner):
    run_checked(runner, [adb, "-s", serial, "shell", "run-as", package, "true"],
                "run-as verification")


def parse_result(result):
    data = result.stdout

    if isinstance(data, str):
        data = data.encode("utf-8")

    require(len(data) <= MAX_RESPONSE_BYTES, "Device response is too large")

    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DeviceError("Device returned malformed JSON") from error

    require(isinstance(value, dict), "Device result must be a JSON object")
    require(value.get("success") is True,
            str(value.get("error") or value.get("message") or "Device operation failed"))
    return value


def install_archive(archive, serial, package="org.fluxgram.messenger.beta", adb="adb",
                    method="install", enable=False, runner=None):
    require(method in {"install", "reload"}, "Invalid device operation")
    runner = runner or ProcessRunner()
    serial = select_device(adb, serial, runner)
    verify_run_as(adb, serial, package, runner)
    archive = Path(archive)
    require(archive.is_file(), f"Package does not exist: {archive}")
    data = archive.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    authority = f"{package}.fluxgram.debug.plugins"
    base_uri = f"content://{authority}"
    run_checked(
        runner,
        [adb, "-s", serial, "shell", "content", "write", "--uri", f"{base_uri}/archive"],
        "archive upload",
        input_data=data,
    )
    run_checked(
        runner,
        [adb, "-s", serial, "shell", "content", "call", "--uri", f"{base_uri}/control",
         "--method", method, "--arg", digest, "--extra", f"enable:b:{str(enable).lower()}"],
        f"device {method}",
    )
    result = run_checked(
        runner,
        [adb, "-s", serial, "shell", "content", "read", "--uri", f"{base_uri}/result"],
        "device result",
    )
    return parse_result(result)


def stream_logs(serial, package="org.fluxgram.messenger.beta", adb="adb", plugin_id=None,
                runner=None, output=None):
    runner = runner or ProcessRunner()
    serial = select_device(adb, serial, runner)
    verify_run_as(adb, serial, package, runner)
    result = run_checked(runner, [adb, "-s", serial, "shell", "pidof", package], "pidof")
    pids = output_text(result).split()
    require(len(pids) == 1 and PID.fullmatch(pids[0]) is not None,
            "FluxGram process is not running or pidof was ambiguous")
    marker = f"[{plugin_id}]" if plugin_id else None
    argv = [adb, "-s", serial, "logcat", "--pid", pids[0], "-v", "threadtime",
            "FluxGramPython:I", "FluxGramDevBridge:I", "*:S"]

    for line in runner.stream(argv):
        if marker is None or marker in line:
            if output is None:
                print(line, end="")
            else:
                output.write(line)
