"""Offline before/after retrieval benchmark. Never opens a model or GitHub connection."""

import argparse
import hashlib
import importlib.util
import json
import math
import platform
import random
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path

from reposcout.retrieval import RepositoryIndex
from reposcout.settings import Settings
from reposcout.storage import Store


def load_baseline():
    path = Path(__file__).parent / "performance/baseline/retrieval.py"
    spec = importlib.util.spec_from_file_location("retrieval_before", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RepositoryIndex, hashlib.sha256(path.read_bytes()).hexdigest()


def describe(values):
    return {
        "p50_ms": statistics.median(values),
        "p95_ms": sorted(values)[math.ceil(len(values) * 0.95) - 1],
        "raw_ms": values,
    }


def measure(files, samples, baseline):
    with tempfile.TemporaryDirectory(prefix="reposcout-benchmark-") as directory:
        store = Store(Settings(home=Path(directory)))
        run = store.create("controlled offline retrieval benchmark", "offline")
        repository, commit = "fixture/benchmark", "a" * 40
        store.put(run, "repository", repository, {"commit": commit})
        after, before = RepositoryIndex(store), baseline(store)
        # Each full document also has a historical file evidence record, as a GitHub read does.
        seed = []
        for i in range(files):
            path = f"src/module_{i:04}.py"
            content = "\n".join(
                f"checkpoint_{i} line {j} supports 断点恢复 and task budget" for j in range(40)
            )
            after.add(repository, commit, path, content)
            value = {
                "kind": "file",
                "repository": repository,
                "commit": commit,
                "path": path,
                "content": "\n".join(
                    f"{j}: {line}" for j, line in enumerate(content.splitlines(), 1)
                ),
            }
            seed.append((run, "evidence", f"seed-{i:04}", json.dumps(value)))
        with store.connect() as db:
            db.executemany("INSERT INTO objects VALUES(?,?,?,?)", seed)
        queries = ["checkpoint", "断点恢复", "absentmarker"]

        def stable(result):
            return {k: v for k, v in result.items() if k != "elapsed_seconds"}

        for query in queries:
            assert stable(before.search(run, repository, query)) == stable(
                after.search(run, repository, query)
            )
        measurements = {"before": [], "after": []}
        randomizer = random.Random(26)
        for i in range(samples + 5):
            query = queries[i % len(queries)]
            order = [("before", before), ("after", after)]
            randomizer.shuffle(order)
            for name, index in order:
                started = time.perf_counter_ns()
                index.search(run, repository, query)
                elapsed = (time.perf_counter_ns() - started) / 1e6
                if i >= 5:
                    measurements[name].append(elapsed)
        stats = {k: describe(v) for k, v in measurements.items()}
        saved = stats["before"]["p50_ms"] - stats["after"]["p50_ms"]
        return {
            "files": files,
            "seed_file_evidence": files,
            "queries": queries,
            "samples_per_variant": samples,
            "warmup_pairs": 5,
            "semantic_equal": True,
            **stats,
            "p50_saved_ms": saved,
            "p50_reduction_percent": 100 * saved / stats["before"]["p50_ms"],
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=40)
    args = parser.parse_args()
    if args.samples < 20:
        parser.error("use at least 20 measured pairs")
    if args.out.exists():
        parser.error("output already exists; choose a new path")
    baseline, sha = load_baseline()
    report = {
        "scope": "local synthetic corpus; full RepositoryIndex.search including SQLite persistence; warm OS cache",
        "excludes": ["LLM inference", "GitHub network", "first-time legacy migration"],
        "platform": platform.platform(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "baseline_sha256": sha,
        "candidate_sha256": hashlib.sha256(
            Path(__import__("reposcout.retrieval", fromlist=["x"]).__file__).read_bytes()
        ).hexdigest(),
        "random_seed": 26,
        "cases": [measure(n, args.samples, baseline) for n in [30, 100, 300]],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            [
                {k: v for k, v in c.items() if k not in {"before", "after", "queries"}}
                | {"before_p50": c["before"]["p50_ms"], "after_p50": c["after"]["p50_ms"]}
                for c in report["cases"]
            ],
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
