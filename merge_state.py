#!/usr/bin/env python3
"""
Merge two sync state files
==========================
Used by the workflow when another run pushed a newer ``synced_tracks.json``
while this run was working. Both sets of synced IDs are kept, so no track is
processed twice and none is forgotten; the ``unmatched`` list comes from the
most recent run, since it reflects the latest search results.

Usage::

    python merge_state.py OURS THEIRS [-o OUTPUT]   # OUTPUT defaults to OURS
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_state_file(path: str | Path) -> dict[str, Any]:
    """Reads a state file; anything unreadable counts as an empty state."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def merge_states(ours: dict[str, Any], theirs: dict[str, Any]) -> dict[str, Any]:
    """Union of synced IDs; ``unmatched`` and ``last_sync`` from the newer side."""
    newest, other = sorted(
        (ours, theirs), key=lambda s: str(s.get("last_sync") or ""), reverse=True
    )
    return {
        "last_sync": newest.get("last_sync") or other.get("last_sync"),
        "synced_yt_ids": sorted(set(ours.get("synced_yt_ids") or []) | set(theirs.get("synced_yt_ids") or [])),
        "unmatched": sorted(set(newest.get("unmatched") or [])),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge two synced_tracks.json files.")
    parser.add_argument("ours", help="state produced by this run")
    parser.add_argument("theirs", help="state currently on the remote branch")
    parser.add_argument("-o", "--output", help="where to write the merged state (default: OURS)")
    args = parser.parse_args(argv)

    merged = merge_states(load_state_file(args.ours), load_state_file(args.theirs))
    output = Path(args.output or args.ours)
    output.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"Merged state written to {output}: {len(merged['synced_yt_ids'])} synced id(s), "
        f"{len(merged['unmatched'])} unmatched."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
