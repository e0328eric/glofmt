"""Keep formatter tests independent of the user's configuration on Unix."""
import os
from pathlib import Path
import sys


def config_environment(root):
    environment = os.environ.copy()
    if sys.platform == "win32":
        # Glosso uses Windows Known Folders, which environment variables cannot
        # redirect. Configuration tests skip instead of changing user files.
        return environment, None
    root = Path(root)
    environment["HOME"] = str(root / "home")
    environment["XDG_CONFIG_HOME"] = str(root / "config")
    base = (root / "home/Library/Application Support" if sys.platform == "darwin"
            else root / "config")
    return environment, base / "glofmt/config.toml"
