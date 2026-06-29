"""Print review node timings from P5 golden / review output JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_payload(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "metadata" in data:
        meta = data.get("metadata") or {}
        if isinstance(meta, dict) and "compliance_stats" in meta:
            return meta
    return data if isinstance(data, dict) else {}


def _find_latest_output(directory: Path) -> Path | None:
    candidates = sorted(directory.glob("*review*p5*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print node_timings_ms from review output JSON")
    parser.add_argument("path", nargs="?", help="Path to *_review_p5.json (default: latest in outputs/)")
    parser.add_argument(
        "--outputs-dir",
        default=None,
        help="Directory to scan for latest review JSON when path omitted",
    )
    args = parser.parse_args(argv)

    if args.path:
        json_path = Path(args.path)
    else:
        out_dir = Path(args.outputs_dir) if args.outputs_dir else Path(__file__).resolve().parents[2] / "temp_java_sync" / "outputs"
        latest = _find_latest_output(out_dir)
        if latest is None:
            print(f"No review JSON found in {out_dir}", file=sys.stderr)
            return 1
        json_path = latest
        print(f"Using {json_path}")

    payload = _load_payload(json_path)
    stats = payload.get("compliance_stats") or payload
    timings = dict(stats.get("node_timings_ms") or {})
    wall = stats.get("review_wall_ms")

    print(f"\n{'node':<28} {'ms':>10}")
    print("-" * 40)
    for node, ms in sorted(timings.items(), key=lambda item: float(item[1]), reverse=True):
        print(f"{node:<28} {float(ms):>10.1f}")
    if wall is not None:
        print("-" * 40)
        print(f"{'review_wall_ms':<28} {float(wall):>10.1f}")

    cache_hits = stats.get("mcp_cache_hits")
    if cache_hits is not None:
        print(
            f"\nmcp_cache: hits={cache_hits} misses={stats.get('mcp_cache_misses')} "
            f"rate={stats.get('mcp_cache_hit_rate')}"
        )
    batches = stats.get("llm_batches_actual")
    if batches is not None:
        print(f"section_compare llm_batches_actual={batches} failed={stats.get('llm_batches_failed', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
