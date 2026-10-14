import subprocess


class ProcessRunner:

    def run(self, argv, input_data=None):
        return subprocess.run(
            [str(item) for item in argv],
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def stream(self, argv):
        process = subprocess.Popen(
            [str(item) for item in argv],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        try:
            if process.stdout is not None:
                yield from process.stdout
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait()
