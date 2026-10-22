"""deploy/requirements.txt pins, with hashes, everything the image and the systemd install get."""

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "deploy" / "requirements.txt"


def _name(requirement: str) -> str:
    return re.split(r"[\s<>=!~;\[]", requirement.strip(), maxsplit=1)[0].lower().replace("_", "-")


def _entries() -> list[list[str]]:
    """Each pin with its hashes. A package can be pinned once per Python version range."""
    entries: list[list[str]] = []
    for line in LOCK.read_text().splitlines():
        if line and not line.startswith((" ", "#")):
            entries.append([line])
        elif entries and line.strip().startswith("--hash="):
            entries[-1].append(line.strip())
    return entries


def test_every_dependency_is_pinned_with_hashes():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    wanted = {_name(r) for r in project["dependencies"]}
    wanted |= {_name(r) for r in (ROOT / "deploy" / "requirements.in").read_text().splitlines()
               if r.strip() and not r.startswith("#")}
    entries = _entries()
    assert wanted <= {_name(lines[0]) for lines in entries}, \
        "run the command at the top of deploy/requirements.txt"
    for lines in entries:
        assert "==" in lines[0], lines[0]
        assert any(line.startswith("--hash=sha256:") for line in lines[1:]), lines[0]
