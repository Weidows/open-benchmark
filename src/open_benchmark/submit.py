"""Submit results as a pull request (leaderboard contributions).

Deliberately thin: results are plain JSONL, so a submission is "add file to the
results/ directory, open a PR". Works from any git checkout with the ``gh`` CLI
authenticated; ``--dry-run`` prints the plan without touching the network.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from open_benchmark.schema import Run


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    return proc


def submit(
    results: list[Path],
    *,
    repo_dir: Path,
    target_dir: str = "results",
    base: str = "main",
    branch: str | None = None,
    title: str | None = None,
    body: str | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> str:
    """Copy runs into ``<repo_dir>/<target_dir>`` and open a PR. Returns the PR URL."""
    if not shutil.which("gh"):
        raise RuntimeError("gh CLI not found; install it or use --dry-run to inspect the plan")
    repo_dir = Path(repo_dir).resolve()
    if not (repo_dir / ".git").exists():
        raise RuntimeError(f"{repo_dir} is not a git checkout")

    runs = _load(results)
    if not runs:
        raise RuntimeError("no runs found in the given paths")
    for run in runs:
        run.device.redact()  # hostnames/serials/raw blobs never leave the machine

    # Validate what is actually being shipped (post-redaction), not the raw file:
    # one bad run must not reach a shared leaderboard.
    from open_benchmark.validate import validate_run

    errors = [
        issue
        for run in runs
        for issue in validate_run(run, source="<submit>")
        if issue.severity == "error"
    ]
    if errors and not force:
        detail = "; ".join(f"{issue.code}: {issue.message}" for issue in errors[:5])
        more = "" if len(errors) <= 5 else f" (+{len(errors) - 5} more)"
        raise RuntimeError(
            f"refusing to submit {len(errors)} validation error(s): {detail}{more}\n"
            "fix the collector, or pass --force to override"
        )

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    fingerprints = {r.device.fingerprint or "unknown" for r in runs}
    branch = branch or f"results/{stamp}-{sorted(fingerprints)[0]}"
    dest_dir = repo_dir / target_dir
    dest = dest_dir / f"{stamp}-{sorted(fingerprints)[0]}.jsonl"
    title = title or f"results: {len(runs)} run(s) from {', '.join(sorted(fingerprints))[:2]}"

    if dry_run:
        return (
            f"[dry-run] would write {dest.relative_to(repo_dir)} "
            f"({sum(len(r.results) for r in runs)} executor results), "
            f"branch {branch} off {base}, then `gh pr create`"
        )

    dest_dir.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as fh:
        for run in runs:
            fh.write(json.dumps(run.to_dict(), ensure_ascii=False) + "\n")

    _run(["git", "-C", str(repo_dir), "fetch", "origin", base])
    _run(["git", "-C", str(repo_dir), "checkout", "-B", branch, f"origin/{base}"])
    _run(["git", "-C", str(repo_dir), "add", str(dest.relative_to(repo_dir))])
    _run(
        [
            "git",
            "-C",
            str(repo_dir),
            "-c",
            "user.name=open-benchmark",
            "-c",
            "user.email=open-benchmark@localhost",
            "commit",
            "-m",
            title,
        ]
    )
    _run(["git", "-C", str(repo_dir), "push", "-u", "origin", branch, "--force"])
    pr = _run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            _repo_slug(repo_dir),
            "--base",
            base,
            "--head",
            branch,
            "--title",
            title,
            "--body",
            body or _default_body(runs),
        ]
    )
    return pr.stdout.strip().splitlines()[-1] if pr.stdout.strip() else "PR created"


def _load(paths: list[Path]) -> list[Run]:
    from open_benchmark.schema import read_runs

    return read_runs([str(p) for p in paths])


def _repo_slug(repo_dir: Path) -> str:
    proc = _run(["git", "-C", str(repo_dir), "remote", "get-url", "origin"])
    url = proc.stdout.strip()
    if url.startswith("git@github.com:"):
        url = url.split(":", 1)[1]
    elif "github.com/" in url:
        url = url.split("github.com/", 1)[1]
    return url.removesuffix(".git").strip("/")


def _default_body(runs: list[Run]) -> str:
    lines = ["Automated hardware benchmark submission.", ""]
    for run in runs:
        dev = run.device
        cpu = (dev.cpu or {}).get("model") or "?"
        total = (dev.memory or {}).get("total_bytes") or 0
        counts = run.status_counts()
        lines.append(
            f"- `{dev.fingerprint}` {cpu} / {total / 1024**3:.1f} GiB / {dev.os} "
            f"({run.suite} suite, ok={counts['ok']} skipped={counts['skipped']} error={counts['error']})"
        )
    lines += ["", "Generated by `open-benchmark submit`."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin wrapper
    from open_benchmark.cli import main as cli_main

    return cli_main(argv if argv is not None else sys.argv[1:])
