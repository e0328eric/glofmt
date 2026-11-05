#!/usr/bin/env python3
"""Build, install, update, and relocate a standalone formatter bundle."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from environment import config_environment

ROOT = Path(__file__).resolve().parents[1]
COMPILER = "glosso"
EXECUTABLE = "glofmt.exe" if sys.platform == "win32" else "glofmt"
LIBRARY = {
    "win32": "glosso.dll",
    "darwin": "libglosso.dylib",
}.get(sys.platform, "libglosso.so")
LIBRARY_DIRECTORY = "bin" if sys.platform == "win32" else "share"


class Installation(unittest.TestCase):
    def build_task(self, *arguments):
        return subprocess.run(
            [COMPILER, "compile", "first.glo", "--cache-dir=build/cache",
             "--color=never",
             "--", *map(str, arguments)],
            cwd=ROOT, capture_output=True, text=True, timeout=180,
        )

    def test_install_update_and_relocate(self):
        with tempfile.TemporaryDirectory(prefix="glofmt-install-") as directory:
            root = Path(directory)
            prefix = root / "prefix with spaces"
            for attempt in range(2):
                installed = self.build_task("install", "--prefix", prefix)
                self.assertEqual(installed.returncode, 0,
                                 installed.stdout + installed.stderr)
                binary = prefix / "bin" / EXECUTABLE
                library = prefix / LIBRARY_DIRECTORY / LIBRARY
                self.assertTrue(binary.is_file())
                self.assertTrue(library.is_file())
                self.assertTrue(os.access(binary, os.X_OK))
                if attempt == 0 and sys.platform != "win32":
                    binary.chmod(0o555)
                    library.chmod(0o444)

            moved = root / "relocated bundle"
            prefix.rename(moved)
            binary = moved / "bin" / EXECUTABLE
            library = moved / LIBRARY_DIRECTORY / LIBRARY
            environment, config_path = config_environment(root)
            for key in ("GLOSSO_STD", "LIBGLOSSO_PATH", "LD_LIBRARY_PATH"):
                environment.pop(key, None)

            formatted = subprocess.run(
                [str(binary)], input=b"main::(){x:=1;}",
                cwd=root, env=environment, capture_output=True, timeout=30,
            )
            self.assertEqual(formatted.returncode, 0, formatted.stderr)
            self.assertEqual(formatted.stdout,
                             b"main :: () {\n    x := 1;\n}\n")

            source = root / "source.glo"
            source.write_bytes(b"main::(){x:=1;}")
            written = subprocess.run(
                [str(binary), str(source)], cwd=root, env=environment,
                capture_output=True, timeout=30,
            )
            self.assertEqual(written.returncode, 0, written.stderr)
            self.assertEqual(written.stdout, b"")
            self.assertEqual(source.read_bytes(), b"main :: () {\n    x := 1;\n}\n")

            if config_path is not None:
                config_path.parent.mkdir(parents=True)
                config_path.write_text('width = 40\n')
                configured = subprocess.run(
                    [str(binary)],
                    input=b'main::(){result:=combine(first_argument,second_argument);}',
                    cwd=root, env=environment, capture_output=True, timeout=30,
                )
                self.assertEqual(configured.returncode, 0, configured.stderr)
                self.assertEqual(
                    configured.stdout,
                    b'main :: () {\n    result := combine(\n        first_argument,\n'
                    b'        second_argument\n    );\n}\n',
                )

            # Prove the installed binary uses its own library, not an absolute
            # SDK path left over from the compiler installation or checkout.
            library.rename(library.with_suffix(".hidden"))
            missing = subprocess.run(
                [str(binary), "--version"], cwd=root, env=environment,
                capture_output=True, timeout=30,
            )
            self.assertNotEqual(missing.returncode, 0)

    def test_invalid_install_arguments(self):
        result = self.build_task("install", "--prefix")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("usage:", result.stdout)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--compiler", default=COMPILER)
    args, rest = parser.parse_known_args()
    COMPILER = shutil.which(args.compiler) or str(Path(args.compiler).resolve())
    unittest.main(argv=[sys.argv[0], *rest])
