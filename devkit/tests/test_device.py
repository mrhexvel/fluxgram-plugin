import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from fluxgram_devkit.device import (
    DeviceError,
    install_archive,
    select_device,
    stream_logs,
)


class FakeRunner:

    def __init__(self, responses, lines=()):
        self.responses = list(responses)
        self.lines = list(lines)
        self.calls = []
        self.stream_calls = []

    def run(self, argv, input_data=None):
        self.calls.append((list(argv), input_data))
        response = self.responses.pop(0)

        if isinstance(response, tuple):
            code, stdout, stderr = response
        else:
            code, stdout, stderr = 0, response, b""

        return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)

    def stream(self, argv):
        self.stream_calls.append(list(argv))
        yield from self.lines


class DeviceTest(unittest.TestCase):

    def test_selects_only_online_device(self):
        runner = FakeRunner([b"List of devices attached\nserial-1\tdevice\nserial-2\toffline\n"])
        self.assertEqual(select_device("adb-custom", None, runner), "serial-1")
        self.assertEqual(runner.calls[0][0], ["adb-custom", "devices"])

        runner = FakeRunner([b"List of devices attached\na\tdevice\nb\tdevice\n"])

        with self.assertRaises(DeviceError):
            select_device("adb", None, runner)

    def test_install_uses_fixed_content_protocol_and_stdin(self):
        result = {"success": True, "plugin_id": "example.test"}
        runner = FakeRunner([
            b"List of devices attached\nserial-1\tdevice\n",
            b"",
            b"Bundle[{result=started}]",
            json.dumps(result).encode(),
        ])

        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "plugin.fgplugin"
            archive.write_bytes(b"archive bytes")
            value = install_archive(
                archive,
                "serial-1",
                "org.fluxgram.messenger",
                "/tools/adb",
                "install",
                True,
                runner,
            )

        authority = "org.fluxgram.messenger.fluxgram.debug.plugins"
        self.assertEqual(value, result)
        self.assertEqual(
            runner.calls[1],
            (["/tools/adb", "-s", "serial-1", "shell", "content", "write", "--uri",
              f"content://{authority}/archive"], b"archive bytes"),
        )
        self.assertEqual(runner.calls[2][0][0:8], [
            "/tools/adb", "-s", "serial-1", "shell", "content", "call", "--uri",
            f"content://{authority}/control",
        ])
        self.assertIn("enable:b:true", runner.calls[2][0])
        self.assertEqual(runner.calls[3][0][-1], f"content://{authority}/result")

    def test_rejects_malformed_and_failed_device_results(self):
        prefix = [
            b"List of devices attached\nserial-1\tdevice\n",
            b"",
            b"",
        ]

        for response in (
                b"not json",
                b"[]",
                b'{"success":false,"error":"denied"}',
                b" " * (64 * 1024 + 1)):
            runner = FakeRunner([*prefix, response])

            with tempfile.TemporaryDirectory() as directory:
                archive = Path(directory) / "plugin.fgplugin"
                archive.write_bytes(b"archive")

                with self.assertRaises(DeviceError):
                    install_archive(archive, "serial-1", runner=runner)

        runner = FakeRunner([
            b"List of devices attached\nserial-1\tdevice\n",
            (1, b"", b"developer access disabled"),
        ])

        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "plugin.fgplugin"
            archive.write_bytes(b"archive")

            with self.assertRaisesRegex(DeviceError, "developer access disabled"):
                install_archive(archive, "serial-1", runner=runner)

    def test_reports_provider_exception_even_when_content_command_succeeds(self):
        runner = FakeRunner([
            b"List of devices attached\nserial-1\tdevice\n",
            (0, b"", b"Error while accessing provider:example\n"
                b"java.lang.SecurityException: Enable temporary plugin developer access\n"
                b"\tat android.os.Parcel.readException(Parcel.java:1)\n"),
        ])

        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "plugin.fgplugin"
            archive.write_bytes(b"archive")

            with self.assertRaisesRegex(DeviceError, "Enable temporary plugin developer access"):
                install_archive(archive, "serial-1", runner=runner)

    def test_streams_filtered_plugin_logs(self):
        runner = FakeRunner([
            b"List of devices attached\nserial-1\tdevice\n",
            b"1234\n",
        ], [
            "one [other.plugin] hidden\n",
            "two [example.test] shown\n",
        ])
        output = io.StringIO()
        stream_logs("serial-1", runner=runner, plugin_id="example.test", output=output)
        self.assertEqual(output.getvalue(), "two [example.test] shown\n")
        self.assertEqual(runner.stream_calls, [[
            "adb", "-s", "serial-1", "logcat", "--pid", "1234", "-v", "threadtime",
            "FluxGramPython:I", "FluxGramDevBridge:I", "*:S",
        ]])

    def test_rejects_ambiguous_pid(self):
        runner = FakeRunner([
            b"List of devices attached\nserial-1\tdevice\n",
            b"1234 5678\n",
        ])

        with self.assertRaises(DeviceError):
            stream_logs("serial-1", runner=runner, plugin_id="example.test")


if __name__ == "__main__":
    unittest.main()
