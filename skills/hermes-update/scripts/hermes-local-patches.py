#!/usr/bin/env python3
"""Hermes local-patch engine.

Reads ``~/.hermes/local-patches/hermes-agent.yaml`` and ensures every
declared patch is committed into ``~/.hermes/hermes-agent`` after an
upstream update.

Designed to be **idempotent**, **non-destructive**, and to **never bail out
on the first conflict** — it processes every patch in the manifest and
returns a structured report at the end.

Usage:
  hermes-local-patches.py status          # print per-patch status, no changes
  hermes-local-patches.py recover         # commit unstaged changes that match a patch
  hermes-local-patches.py apply           # full flow: recover -> verify -> cherry-pick missing
  hermes-local-patches.py apply --json    # JSON-formatted report (for scripts)

Exit codes:
  0  - all patches OK or successfully recovered/restored
  1  - one or more patches still unaccounted for (conflict, missing ref, etc.)
  2  - manifest missing or invalid

Output line conventions (one per patch, prefixed with the verb):
  PATCH_OK          <name>                   marker present, nothing to do
  PATCH_RECOMMITTED <name> from <files>      unstaged changes were committed as this patch
  PATCH_RESTORED    <name> from <ref>        cherry-pick of <ref> succeeded
  PATCH_CONFLICT    <name> from <ref>        cherry-pick conflicted; aborted, working tree clean
  PATCH_MISSING_REF <name>                   no candidate ref reachable; manual fix required
  PATCH_SKIPPED     <name> <reason>          patch declared optional or pre-conditions unmet

A run summary line is printed last:
  PATCH_SUMMARY ok=N recommitted=N restored=N conflict=N missing_ref=N skipped=N
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

REPO = pathlib.Path.home() / ".hermes" / "hermes-agent"
MANIFEST = pathlib.Path.home() / ".hermes" / "local-patches" / "hermes-agent.yaml"
HISTORY = pathlib.Path.home() / ".hermes" / "local-patches" / ".applied-history.json"


def _git(*args: str, check: bool = False, capture: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in the repo directory, returning the CompletedProcess."""
    return subprocess.run(
        ["git", *args],
        cwd=REPO,
        capture_output=capture,
        text=True,
        check=check,
    )


def _ref_exists(ref: str) -> bool:
    if not ref:
        return False
    return _git("cat-file", "-e", f"{ref}^{{commit}}").returncode == 0


def _commit_message(ref: str) -> str:
    cp = _git("log", "-1", "--format=%s", ref)
    return cp.stdout.strip() if cp.returncode == 0 else ""


def _file_text_at_head(path: pathlib.Path) -> str:
    rel = path.relative_to(REPO)
    cp = _git("show", f"HEAD:{rel.as_posix()}")
    return cp.stdout if cp.returncode == 0 else ""


def _file_text_in_worktree(path: pathlib.Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(errors="ignore")
    except Exception:
        return ""


def _unstaged_files() -> List[str]:
    cp = _git("status", "--porcelain")
    if cp.returncode != 0:
        return []
    out: List[str] = []
    for line in cp.stdout.splitlines():
        if not line or len(line) < 4:
            continue
        # Format: "XY <path>" — X = staged, Y = worktree
        out.append(line[3:].strip())
    return out


def _load_manifest() -> Dict[str, Any]:
    if not MANIFEST.exists():
        print(f"PATCH_MANIFEST_MISSING {MANIFEST}", file=sys.stderr)
        sys.exit(2)
    try:
        import yaml
    except ImportError:
        print("PATCH_MANIFEST_ERROR pyyaml not installed", file=sys.stderr)
        sys.exit(2)
    try:
        data = yaml.safe_load(MANIFEST.read_text()) or {}
    except Exception as exc:
        print(f"PATCH_MANIFEST_INVALID {exc}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(data, dict) or not isinstance(data.get("patches"), list):
        print("PATCH_MANIFEST_INVALID expected `patches:` list", file=sys.stderr)
        sys.exit(2)
    return data


def _load_history() -> Dict[str, List[str]]:
    if not HISTORY.exists():
        return {}
    try:
        return json.loads(HISTORY.read_text()) or {}
    except Exception:
        return {}


def _save_history(hist: Dict[str, List[str]]) -> None:
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(hist, ensure_ascii=False, indent=2))


def _record_history(name: str, sha: str) -> None:
    hist = _load_history()
    entries = hist.setdefault(name, [])
    if sha in entries:
        entries.remove(sha)
    entries.insert(0, sha)
    hist[name] = entries[:8]
    _save_history(hist)


def _candidates_for(patch: Dict[str, Any]) -> List[str]:
    """Return refs to try, newest-first.

    Order: history (newest first) -> manifest commit_candidates -> manifest's
    name-derived branch -> nothing.
    """
    hist = _load_history().get(patch["name"], [])
    manifest_cands = patch.get("commit_candidates") or []
    seen = set()
    out: List[str] = []
    for ref in [*hist, *manifest_cands]:
        if not ref or ref in seen:
            continue
        seen.add(ref)
        out.append(ref)
    return out


def _marker_in_text(text: str, regex: str) -> bool:
    if not regex:
        return False
    try:
        return bool(re.search(regex, text, re.M))
    except re.error:
        return False


# ---------------------------------------------------------------------------
# Status checks
# ---------------------------------------------------------------------------


def _patch_state(patch: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Return (state, evidence) for a patch.

    State values:
      - 'committed'      marker matches in HEAD's tree
      - 'unstaged'       marker matches only in the worktree (autostash leftover)
      - 'missing'        marker not found anywhere
    """
    marker_file = REPO / patch["marker_file"]
    marker_regex = patch["marker_regex"]

    head_text = _file_text_at_head(marker_file)
    if _marker_in_text(head_text, marker_regex):
        return ("committed", [])

    worktree_text = _file_text_in_worktree(marker_file)
    if _marker_in_text(worktree_text, marker_regex):
        return ("unstaged", [str(marker_file.relative_to(REPO))])

    return ("missing", [])


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def _commit_unstaged_as_patch(patch: Dict[str, Any]) -> Tuple[bool, str]:
    """Stage all of this patch's `touched_files` (only those modified in worktree)
    and commit them with the patch's commit_message.

    Returns (success, info).  info is either the new commit SHA or an error
    string.
    """
    touched: List[str] = patch.get("touched_files") or [patch["marker_file"]]
    unstaged = set(_unstaged_files())
    matched = [f for f in touched if f in unstaged]
    if not matched:
        # The marker was found in worktree but no touched_files are dirty —
        # this is suspicious.  Fall back to staging all touched files that
        # have any kind of change.
        for f in touched:
            full = REPO / f
            if full.exists():
                matched.append(f)
    if not matched:
        return False, "no touched files modified in worktree"

    # Stage exactly the touched files belonging to this patch.
    add = _git("add", "--", *matched)
    if add.returncode != 0:
        return False, f"git add failed: {add.stderr.strip()}"

    msg = patch.get("commit_message") or f"local: re-apply {patch['name']} after update"
    commit = _git("commit", "-m", msg)
    if commit.returncode != 0:
        # Roll the index back so we don't leave a half-staged state.
        _git("restore", "--staged", "--", *matched)
        return False, f"git commit failed: {commit.stderr.strip()}"

    sha = _git("rev-parse", "--short", "HEAD").stdout.strip()
    _record_history(patch["name"], sha)
    return True, sha


def _try_cherry_pick(patch: Dict[str, Any], ref: str) -> Tuple[bool, str]:
    """Try ``git cherry-pick <ref>`` and commit with the patch's message.

    Returns (success, info).  On failure, the cherry-pick is aborted and
    the working tree restored.
    """
    cp = _git("cherry-pick", "--no-commit", ref)
    if cp.returncode != 0:
        # Abort cleanly to avoid leaving conflict markers.
        ab = _git("cherry-pick", "--abort")
        if ab.returncode != 0:
            # Last-resort: hard reset of the cherry-pick state.
            _git("reset", "--hard", "HEAD")
        # Surface the conflicting paths for diagnostics.
        diff = _git("diff", "--name-only", "--diff-filter=U")
        files = diff.stdout.strip()
        return False, files or cp.stderr.strip() or "conflict"

    # Detect "nothing to commit" — the patch was already in HEAD via some
    # other route (e.g. upstream cherry-picked our fix).  Treat as success.
    status = _git("status", "--porcelain")
    if not status.stdout.strip():
        return True, "noop (already in HEAD)"

    msg = patch.get("commit_message") or f"local: re-apply {patch['name']} after update"
    commit = _git("commit", "-m", msg)
    if commit.returncode != 0:
        return False, f"git commit failed: {commit.stderr.strip()}"

    sha = _git("rev-parse", "--short", "HEAD").stdout.strip()
    _record_history(patch["name"], sha)
    return True, f"{ref} -> {sha}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cmd_status(_args: argparse.Namespace) -> int:
    data = _load_manifest()
    patches = data.get("patches", [])
    bad = 0
    for p in patches:
        state, evidence = _patch_state(p)
        line = f"PATCH_{state.upper()} {p['name']}"
        if evidence:
            line += " worktree=" + ",".join(evidence)
        print(line)
        if state != "committed":
            bad += 1
    return 0 if bad == 0 else 1


def cmd_apply(args: argparse.Namespace) -> int:
    data = _load_manifest()
    patches = data.get("patches", [])

    counts = {"ok": 0, "recommitted": 0, "restored": 0, "conflict": 0, "missing_ref": 0, "skipped": 0}
    report: List[Dict[str, str]] = []
    # Track which patches have been resolved so the second pass skips them.
    resolved: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Pass 1: recover unstaged changes that already match a patch marker.
    # Doing this BEFORE any cherry-pick attempt keeps the working tree
    # clean for pass 2 — otherwise an unstaged file that belongs to
    # patch B blocks pass 2 from cherry-picking patch A and we end up
    # reporting bogus "working_tree_dirty" conflicts for unrelated
    # patches.
    # ------------------------------------------------------------------
    for p in patches:
        name = p["name"]
        state, _ = _patch_state(p)
        if state == "committed":
            resolved[name] = "ok"
            counts["ok"] += 1
            print(f"PATCH_OK {name}")
            report.append({"name": name, "state": "ok"})
            continue
        if state == "unstaged":
            ok, info = _commit_unstaged_as_patch(p)
            if ok:
                resolved[name] = "recommitted"
                counts["recommitted"] += 1
                print(f"PATCH_RECOMMITTED {name} sha={info}")
                report.append({"name": name, "state": "recommitted", "info": info})
            else:
                # Recommit failed; pass 2 may still be able to cherry-pick.
                print(f"PATCH_RECOMMIT_FAILED {name} reason={info}", file=sys.stderr)

    # ------------------------------------------------------------------
    # Pass 2: cherry-pick any patch still not resolved.
    # ------------------------------------------------------------------
    for p in patches:
        name = p["name"]
        if name in resolved:
            continue

        # If pass 1 left committed/unstaged state stale (e.g. one patch's
        # cherry-pick committed code that contains another patch's marker
        # too), re-check the marker before attempting a second cherry-pick.
        state, _ = _patch_state(p)
        if state == "committed":
            resolved[name] = "ok"
            counts["ok"] += 1
            print(f"PATCH_OK {name}")
            report.append({"name": name, "state": "ok"})
            continue

        if _unstaged_files():
            counts["conflict"] += 1
            print(f"PATCH_CONFLICT {name} reason=working_tree_dirty")
            report.append({"name": name, "state": "conflict", "info": "dirty working tree"})
            continue

        cands = _candidates_for(p)
        ref: Optional[str] = next((c for c in cands if _ref_exists(c)), None)
        if not ref:
            counts["missing_ref"] += 1
            print(f"PATCH_MISSING_REF {name} candidates={cands}")
            report.append({"name": name, "state": "missing_ref", "info": ",".join(cands)})
            continue

        ok, info = _try_cherry_pick(p, ref)
        if ok:
            resolved[name] = "restored"
            counts["restored"] += 1
            print(f"PATCH_RESTORED {name} {info}")
            report.append({"name": name, "state": "restored", "info": info})
        else:
            counts["conflict"] += 1
            print(f"PATCH_CONFLICT {name} ref={ref} files={info}")
            report.append({"name": name, "state": "conflict", "info": f"{ref}: {info}"})

    summary = " ".join(f"{k}={v}" for k, v in counts.items())
    print(f"PATCH_SUMMARY {summary}")

    if args.json:
        print(json.dumps({"counts": counts, "patches": report}, ensure_ascii=False, indent=2))

    # Non-zero exit only when something is still unaccounted for.
    return 0 if (counts["conflict"] == 0 and counts["missing_ref"] == 0) else 1


def cmd_recover(_args: argparse.Namespace) -> int:
    """Stand-alone recover phase: commit unstaged changes that match a patch.

    Useful from the SKILL flow before ``hermes update``.
    """
    data = _load_manifest()
    patches = data.get("patches", [])
    any_done = False
    for p in patches:
        state, _ = _patch_state(p)
        if state != "unstaged":
            continue
        ok, info = _commit_unstaged_as_patch(p)
        if ok:
            any_done = True
            print(f"PATCH_RECOMMITTED {p['name']} sha={info}")
        else:
            print(f"PATCH_RECOMMIT_FAILED {p['name']} reason={info}", file=sys.stderr)
    if not any_done:
        print("PATCH_RECOVER nothing to recover")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="print per-patch state, exit 0 if all OK")

    p_apply = sub.add_parser("apply", help="recover + verify + cherry-pick missing")
    p_apply.add_argument("--json", action="store_true", help="emit a JSON report at the end")

    sub.add_parser("recover", help="only commit unstaged changes that match a patch")

    args = parser.parse_args(argv)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "apply":
        return cmd_apply(args)
    if args.cmd == "recover":
        return cmd_recover(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
