"""hwbench command line interface.

Designed for agents first: every command has a ``--json`` form that writes
machine-readable output to stdout (``--json -``) and keeps human text on
stderr, so ``hwbench run --suite quick --json - | jq .results`` always works.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from hwbench import __version__
from hwbench.executors import SUITES, names, resolve
from hwbench.executors.base import Context
from hwbench.executors.native import Toolchain
from hwbench.report import format_run, leaderboard
from hwbench.schema import Result, Run, read_runs, runtime_env, write_run
from hwbench.submit import submit
from hwbench.sysinfo import collect as collect_device

EXIT_OK = 0
EXIT_ERROR = 1


def _split(values: list[str]) -> list[str]:
    """Allow '--only cpu,memory' as well as repeated flags."""
    return [item.strip() for value in values for item in value.split(",") if item.strip()]


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    started = time.time()
    workdir = Path(args.workdir).expanduser()
    disk_path = Path(args.disk_path).expanduser() if args.disk_path else workdir
    scale = args.scale if args.scale is not None else (3.0 if args.suite == "full" else 1.0)

    device = collect_device()
    toolchain = Toolchain.detect() if not args.no_native else Toolchain(None, "disabled")
    ctx = Context(
        suite=args.suite,
        repeat=args.repeat,
        workdir=workdir,
        disk_path=disk_path,
        scale=scale,
        use_native=not args.no_native,
        log=lambda msg: print(msg, file=sys.stderr),
        native=toolchain,
    )

    executors = resolve(_split(args.only), _split(args.exclude), args.suite)
    run = Run(
        suite=args.suite,
        device=device,
        env={
            **runtime_env(),
            "hwbench": __version__,
            "native_toolchain": toolchain.version if toolchain.cmd else None,
            "scale": scale,
            "repeat": args.repeat,
        },
    )
    if toolchain.cmd is None and not args.no_native:
        run.notes.append(f"native kernels unavailable: {toolchain.error or 'no C compiler found'}")

    for ex in executors:
        print(f"-> {ex.name}", file=sys.stderr)
        t0 = time.perf_counter()
        try:
            ok, reason = ex.available(ctx)
            result = ex.run(ctx) if ok else Result(name=ex.name, status="skipped", error=reason)
        except Exception as exc:  # noqa: BLE001 - one broken executor must not kill the run
            result = Result(name=ex.name, status="error", error=f"{type(exc).__name__}: {exc}")
        result.duration_s = round(time.perf_counter() - t0, 3)
        run.results.append(result)
        print(f"   {result.status} in {result.duration_s}s", file=sys.stderr)
    run.ended_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    run.env["wall_seconds"] = round(time.time() - started, 2)

    human = format_run(run, show_samples=args.verbose)
    if args.json == "-":
        sys.stdout.write(write_run(run, None) + "\n")
        if not args.quiet:
            print(human, file=sys.stderr)
    else:
        if not args.quiet:
            print(human)
        if args.json:
            write_run(run, args.json, jsonl=True)
            print(f"\nresults appended to {args.json}", file=sys.stderr)
    counts = run.status_counts()
    return EXIT_ERROR if counts["error"] else EXIT_OK


# --------------------------------------------------------------------------
# devices / executors / schema
# --------------------------------------------------------------------------


def cmd_devices(args: argparse.Namespace) -> int:
    device = collect_device()
    payload = json.dumps(device.to_dict(), ensure_ascii=False, indent=2)
    if args.json == "-" or args.json is None:
        print(payload)
        return EXIT_OK
    Path(args.json).write_text(payload, encoding="utf-8")
    print(f"device written to {args.json}")
    return EXIT_OK


def cmd_executors(args: argparse.Namespace) -> int:
    rows = []
    for name in names():
        suites = [s for s, members in SUITES.items() if name in members]
        ex = resolve([name], suite="full")[0]
        rows.append({"name": name, "tags": list(ex.tags), "suites": suites})
    if args.json == "-":
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            print(f"{row['name']:<22} {','.join(row['tags']):<28} suites={','.join(row['suites'])}")
    return EXIT_OK


def cmd_schema(args: argparse.Namespace) -> int:
    from hwbench.schema import SCHEMA_VERSION

    doc = {
        "schema_version": SCHEMA_VERSION,
        "example_run": Run(device=collect_device(), suite="quick").to_dict(),
    }
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return EXIT_OK


# --------------------------------------------------------------------------
# leaderboard / submit
# --------------------------------------------------------------------------


def cmd_leaderboard(args: argparse.Namespace) -> int:
    runs = read_runs([str(p) for p in args.paths])
    if not runs:
        print("no runs found", file=sys.stderr)
        return EXIT_ERROR
    markdown = leaderboard(runs, bound=args.bound, metrics=args.metric, suite=args.suite)
    if args.json == "-":
        payload = {
            "devices": sorted({r.device.fingerprint for r in runs}),
            "runs": len(runs),
            "markdown": markdown,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(markdown)
    if args.out:
        Path(args.out).write_text(markdown + "\n", encoding="utf-8")
        print(f"leaderboard written to {args.out}", file=sys.stderr)
    return EXIT_OK


def cmd_submit(args: argparse.Namespace) -> int:
    print(
        submit(
            [Path(p) for p in args.paths],
            repo_dir=Path(args.repo_dir),
            target_dir=args.target_dir,
            base=args.base,
            branch=args.branch,
            title=args.title,
            body=args.body,
            dry_run=args.dry_run,
        )
    )
    return EXIT_OK


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hwbench",
        description="Agent-first hardware benchmark harness (CPU / memory / disk) with a stable JSON schema.",
    )
    parser.add_argument("--version", action="version", version=f"hwbench {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run benchmarks and emit a run document")
    run.add_argument("--suite", default="quick", choices=sorted(SUITES))
    run.add_argument("--only", action="append", default=[], help="executor name/prefix (repeatable)")
    run.add_argument("--exclude", action="append", default=[], help="executor name/prefix to drop")
    run.add_argument("--repeat", type=int, default=3, help="samples per executor (default 3)")
    run.add_argument("--scale", type=float, default=None, help="multiplier for workload sizes")
    run.add_argument("--json", default=None, help="JSONL output path, or '-' for stdout")
    run.add_argument("--workdir", default=str(Path.home() / ".cache" / "hwbench" / "work"))
    run.add_argument("--disk-path", default=None, help="directory for disk tests (default: workdir)")
    run.add_argument("--no-native", action="store_true", help="skip the optional C kernels")
    run.add_argument("--quiet", action="store_true")
    run.add_argument("--verbose", action="store_true", help="include raw samples")
    run.set_defaults(func=cmd_run)

    devices = sub.add_parser("devices", help="print the detected device fingerprint")
    devices.add_argument("--json", default="-", help="output path, or '-' for stdout")
    devices.set_defaults(func=cmd_devices)

    ex = sub.add_parser("executors", help="list available executors")
    ex.add_argument("--json", default=None)
    ex.set_defaults(func=cmd_executors)

    schema = sub.add_parser("schema", help="print the schema id and an example document")
    schema.set_defaults(func=cmd_schema)

    board = sub.add_parser("leaderboard", help="aggregate JSONL runs into a markdown table")
    board.add_argument("paths", nargs="+", help="files, directories or globs")
    board.add_argument("--bound", default="hardware", choices=["hardware", "interpreter", "mixed", "all"])
    board.add_argument("--suite", default=None, choices=sorted(SUITES), help="only pool runs from this suite")
    board.add_argument("--metric", action="append", default=[], help="substring filter on metric keys")
    board.add_argument("--out", default=None, help="write markdown here")
    board.add_argument("--json", default=None, help="'-' to print a JSON wrapper instead of markdown")
    board.set_defaults(func=cmd_leaderboard)

    sub_p = sub.add_parser("submit", help="open a PR adding results to a results repository")
    sub_p.add_argument("paths", nargs="+")
    sub_p.add_argument("--repo-dir", default=".", help="git checkout of the leaderboard repo")
    sub_p.add_argument("--target-dir", default="results")
    sub_p.add_argument("--base", default="main")
    sub_p.add_argument("--branch", default=None)
    sub_p.add_argument("--title", default=None)
    sub_p.add_argument("--body", default=None)
    sub_p.add_argument("--dry-run", action="store_true")
    sub_p.set_defaults(func=cmd_submit)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
