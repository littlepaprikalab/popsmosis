"""Install a requirements file so that one bad download cannot keep the rest out, then say what is still missing.

    python tools/install_requirements.py studio/requirements.txt

The session hook (.claude/hooks/session-start.sh) runs this. pip installs a requirements file all or nothing, and
on Nov 4 one wheel came down corrupted (av 19.0.2, which faster-whisper needs: its sha256 was not PyPI's), so
nothing new in the file had been installed since Nov 2, faster-whisper included, while the packages cached before
hid it. So:

    1. the whole file, as before;
    2. the whole file again, with no cache, after a pause: a download cut short is cached as it came;
    3. each requirement on its own, with no cache, so the rest still land around one that will not;
    4. every requirement checked against what is installed (name and version range), whatever pip said.

Exit 0 when every requirement is installed in its range, 1 when one is not; each missing one is printed on stderr.
Uses the standard library and pip alone: it runs before anything else is installed.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from importlib import metadata
from pathlib import Path

try:
    from packaging.requirements import InvalidRequirement, Requirement
except ImportError:  # a bare interpreter: pip's own copy
    from pip._vendor.packaging.requirements import InvalidRequirement, Requirement

PAUSE_S = 15.0


def say(line: str) -> None:
    print(f"install_requirements: {line}", file=sys.stderr, flush=True)


def parse(path: Path) -> tuple[list[str], list[str]]:
    """The file's options (--extra-index-url ...) as pip arguments, and its requirement lines, comments stripped."""
    options, specs = [], []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split(" #", 1)[0].split("\t#", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-"):
            options += line.split()
        else:
            specs.append(line)
    return options, specs


def pip(*args: str) -> bool:
    return subprocess.run([sys.executable, "-m", "pip", "install", "-q", *args]).returncode == 0


def missing(specs: list[str]) -> list[tuple[str, str]]:
    """Each requirement not installed in its version range, with why (markers that do not apply here are skipped)."""
    out = []
    for spec in specs:
        try:
            req = Requirement(spec)
        except InvalidRequirement:
            out.append((spec, "not a requirement pip reads"))
            continue
        if req.marker is not None and not req.marker.evaluate():
            continue
        try:
            have = metadata.version(req.name)
        except metadata.PackageNotFoundError:
            out.append((spec, "not installed"))
            continue
        if req.specifier and not req.specifier.contains(re.sub(r"\+.*$", "", have), prereleases=True):
            out.append((spec, f"installed {have}"))
    return out


def install(path: Path, pause_s: float = PAUSE_S) -> list[tuple[str, str]]:
    """Install the file, step by step as the module says; the requirements still missing after it all."""
    options, specs = parse(path)
    if not pip("-r", str(path)):
        say(f"pip could not install {path} whole; again with no cache in {pause_s:.0f}s")
        time.sleep(pause_s)
        if not pip("--no-cache-dir", "-r", str(path)):
            say("again it could not; each requirement on its own, so one that will not install keeps out no other")
            for spec, _ in missing(specs):
                pip("--no-cache-dir", *options, spec)
    return missing(specs)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    left = install(Path(argv[0]))
    for spec, why in left:
        say(f"MISSING {spec} ({why})")
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
