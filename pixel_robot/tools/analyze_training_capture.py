#!/usr/bin/env python3
"""Validate, fit, and graph one Pixel run or training-capture ZIP."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

from fit_sim_from_run_data import fit_path
from inspect_run_data import summarize_run
from plot_run_data import plot_run


def _write_dashboard(
    output_dir: Path,
    source: Path,
    servo_id: int,
    graphs: list[Path],
) -> Path:
    cards = "\n".join(
        f'''<section><h2>{html.escape(path.stem.replace("-", " ").title())}</h2>
<a class="full" href="{html.escape(path.relative_to(output_dir).as_posix())}" target="_blank">Open full size</a>
<div class="chart"><img src="{html.escape(path.relative_to(output_dir).as_posix())}" alt="{html.escape(path.stem)}"></div></section>'''
        for path in graphs
    )
    dashboard = output_dir / "index.html"
    dashboard.write_text(
        f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Robot run graphs</title>
<style>
body{{font:16px system-ui,sans-serif;max-width:1400px;margin:auto;padding:20px;background:#f5f5f5;color:#171717}}
h1{{margin-bottom:4px}} p{{margin-top:0;color:#555;overflow-wrap:anywhere}}
section{{background:white;margin:20px 0;padding:16px;border:1px solid #ddd;border-radius:8px}}
h2{{font-size:18px;margin:0;text-transform:capitalize}} .full{{display:inline-block;margin:6px 0 12px}}
.chart{{overflow-x:auto}} img{{display:block;width:100%;height:auto}}
</style></head><body>
<h1>Robot run graphs</h1>
<p>{html.escape(source.name)} · selected servo ID {servo_id}</p>
{cards}
</body></html>\n''',
        encoding="utf-8",
    )
    return dashboard


def analyze_capture(source: Path, output_dir: Path, servo_id: int) -> dict[str, Any]:
    if servo_id not in range(1, 13):
        raise ValueError("servo ID must be from 1 through 12")
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize_run(source)
    fit = fit_path(source)
    if not fit["runs"]:
        raise ValueError("capture has no complete policy run with at least two frames")
    graphs = plot_run(source, output_dir / "graphs", servo_id)
    dashboard = _write_dashboard(output_dir, source, servo_id, graphs)
    summary_path = output_dir / "run-summary.json"
    fit_path_output = output_dir / "simulation-fit.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    fit_path_output.write_text(
        json.dumps(fit, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    result = {
        "source": str(source),
        "servo_id": servo_id,
        "transport_50hz_gate": fit["runs"][0]["transport_50hz_gate"],
        "summary": str(summary_path),
        "simulation_fit": str(fit_path_output),
        "graphs": [str(path) for path in graphs],
        "dashboard": str(dashboard),
    }
    (output_dir / "analysis-index.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="run JSONL or training-capture ZIP")
    parser.add_argument("--servo-id", type=int, default=1)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--require-50hz",
        action="store_true",
        help="return failure after writing results if the physical 50 Hz gate does not pass",
    )
    args = parser.parse_args()
    output_dir = args.output_dir or args.source.with_suffix("").with_name(
        args.source.stem + "-analysis"
    )
    try:
        result = analyze_capture(args.source, output_dir, args.servo_id)
    except (OSError, ValueError) as error:
        parser.exit(2, f"error: {error}\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.require_50hz and not result["transport_50hz_gate"]["passed"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
