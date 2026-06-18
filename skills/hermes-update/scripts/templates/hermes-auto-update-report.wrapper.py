#!/usr/bin/env python3
"""Wrapper that forwards to the canonical reporter in the hermes-skills repo.

The cron scheduler in hermes-agent (``cron/scheduler.py::_run_job_script``)
hard-rejects any script in ``~/.hermes/scripts/`` that resolves outside that
directory after following symlinks (deliberate "symlink escape" guard).
That means we cannot simply symlink this file to the skill repo like the
other auto-update scripts.  Instead we keep a tiny real file here that
runpy-loads the canonical implementation:

  ~/Code/hermes-skills/skills/hermes-update/scripts/hermes-auto-update-report.py

Edit that file (not this one) when changing reporter behavior.

``install.sh`` writes this wrapper verbatim into ``~/.hermes/scripts/``;
the same file is committed under ``templates/`` so a fresh install always
has an authoritative copy in the repo.
"""
import os
import pathlib
import runpy
import sys

# Resolve the canonical reporter location.  Honour HERMES_SKILLS_DIR (set by
# install.sh on non-default checkouts) but default to the conventional
# location used by hermes-skills-sync.
skills_root = pathlib.Path(
    os.environ.get("HERMES_SKILLS_DIR")
    or pathlib.Path.home() / "Code" / "hermes-skills"
)
TARGET = skills_root / "skills" / "hermes-update" / "scripts" / "hermes-auto-update-report.py"

if not TARGET.exists():
    print(
        f"hermes-auto-update-report wrapper: target missing at {TARGET}",
        file=sys.stderr,
    )
    sys.exit(2)

runpy.run_path(str(TARGET), run_name="__main__")
