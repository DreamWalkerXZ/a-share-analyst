"""Batch evaluation runner: generates reports for all companies in eval/clean_reports/."""

import argparse
import re
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path so "src.*" imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

REPORTS_DIR = Path("eval/clean_reports")
OUTPUT_DIR = Path("eval/output")
PERIOD = "2025Q4"


def parse_companies() -> list[tuple[str, str, str]]:
    """Extract unique (sector, code, name) from clean report filenames."""
    companies: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for f in sorted(REPORTS_DIR.iterdir()):
        if not f.name.endswith(".md"):
            continue
        m = re.match(r"(.+?)-(\d{6})-(.+?)-2025Q4-", f.name)
        if m:
            key = (m.group(1), m.group(2), m.group(3))
            if key not in seen:
                seen.add(key)
                companies.append(key)
    return companies


def run_one(sector: str, code: str, name: str, run_id: int) -> bool:
    """Run pipeline for a single company. Returns True on success."""
    from src.agent.graph import build_graph

    prefix = f"{sector}-{code}-{name}-{PERIOD}-AShareAnalyst-{run_id}"

    graph = build_graph()
    try:
        state = graph.invoke({
            "company": name,
            "stock_code": code,
            "period": PERIOD,
            "collected_data": {},
            "sections": {},
            "output_path": "",
            "eval_output_dir": str(OUTPUT_DIR),
            "eval_prefix": prefix,
        })
        print(f"  [OK] {prefix}")
        return True
    except Exception as e:
        print(f"  [FAIL] {prefix}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Batch eval runner")
    parser.add_argument("--companies", type=int, default=0, help="Max companies to run (0=all)")
    parser.add_argument("--runs", type=int, default=3, help="Runs per company (default: 3)")
    parser.add_argument("--skip-existing", action="store_true", help="Skip already-completed runs")
    args = parser.parse_args()

    companies = parse_companies()
    if args.companies > 0:
        companies = companies[: args.companies]

    total = len(companies) * args.runs
    print(f"Companies: {len(companies)}, Runs each: {args.runs}, Total: {total}")
    print(f"Output dir: {OUTPUT_DIR}")
    print()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    start = time.time()
    ok, fail, skip = 0, 0, 0
    for run_id in range(1, args.runs + 1):
        print(f"\n{'='*40}\nRun {run_id}/{args.runs}\n{'='*40}")
        for i, (sector, code, name) in enumerate(companies):
            prefix = f"{sector}-{code}-{name}-{PERIOD}-AShareAnalyst-{run_id}"
            if args.skip_existing:
                report_path = OUTPUT_DIR / f"{prefix}.md"
                phase1_path = OUTPUT_DIR / f"{prefix}-Phase1.json"
                phase2_path = OUTPUT_DIR / f"{prefix}-Phase2.json"
                if report_path.exists() and phase1_path.exists() and phase2_path.exists():
                    print(f"  [SKIP] [{i + 1}/{len(companies)}] {name} ({code})")
                    skip += 1
                    continue
            print(f"[{i + 1}/{len(companies)}] {name} ({code})")
            if run_one(sector, code, name, run_id):
                ok += 1
            else:
                fail += 1

    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.0f}s — OK: {ok}, FAIL: {fail}, SKIP: {skip}")


if __name__ == "__main__":
    main()
