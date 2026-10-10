"""Which release is installed.

The release zip carries a VERSION file (written by the release workflow, and
again by the updater after every update). Without it - a git checkout or a
zip from "Code -> Download ZIP" - the version is "dev".
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
FILE = os.path.join(HERE, "VERSION")


def get():
    """-> "v1.2.3", or "" when unknown."""
    try:
        with open(FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def write(tag):
    try:
        with open(FILE, "w", encoding="utf-8") as f:
            f.write(tag + "\n")
    except OSError:
        pass


def label():
    return get() or "dev"
