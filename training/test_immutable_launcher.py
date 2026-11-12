"""A source deployment cannot alter a running shell's remaining instructions."""
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which("bash"), "requires bash")
class ImmutableLauncherTests(unittest.TestCase):
    def test_source_replacement_preserves_arguments_cwd_and_completion(self):
        production = (Path(__file__).parent / "run_simple_dog.sh").read_text()
        bootstrap = production.split("# BEGIN immutable launcher\n", 1)[1].split(
            "# END immutable launcher", 1)[0]
        with tempfile.TemporaryDirectory(prefix="robot launcher ") as directory:
            root = Path(directory)
            script = root / "run_simple_dog.sh"
            original = ("#!/bin/bash\nset -Eeuo pipefail\nreadonly TRAINING_ROOT="
                        + shlex.quote(str(root)) + "\n" + bootstrap
                        + "printf 'ready\\n'\nread -r release\n"
                        + "# Padding beyond bash's input buffer\n" * 2000
                        + 'printf "%s|%s|%s\\n" "$1" "$release" "$PWD"\n')
            script.write_text(original)
            process = subprocess.Popen(["bash", str(script), "argument with spaces"],
                                       cwd=root, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(process.stdout.readline(), "ready\n")
                script.write_text("#!/bin/bash\nexit 73\n")
                output, error = process.communicate("continue\n", timeout=5)
                self.assertEqual(process.returncode, 0, error)
                self.assertEqual(output, f"argument with spaces|continue|{root}\n")
                frozen = list((root / "runs/launchers").glob("*.sh"))
                self.assertEqual(len(frozen), 1)
                self.assertEqual(frozen[0].read_text(), original)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    unittest.main()
