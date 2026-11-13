"""Run the intelligence benchmark against a running ULTRON backend and score it.

Every question is a fresh conversation sent through the real /v1/chat path (personality,
routing, memory block and all), so the score is the score of the whole system, not of a bare
model. Answers are checked by code (bench/scoring.py).

    PYTHONPATH=. .venv/bin/python scripts/bench_intelligence.py --url http://127.0.0.1:18990 \
        --token-file TOKEN --set dev --privacy local_only --label baseline

--privacy local_only forces the on-device model (free, private). --privacy default lets the
router use the cloud and SPENDS the daily token budget: it prints an estimate and refuses more
than --max-cloud requests.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx

from bench.scoring import check, load

BENCH = Path(__file__).resolve().parents[1] / "bench"
EST_TOKENS_PER_CLOUD_QUESTION = 1700  # measured prompt ~1.3-1.5K + short answer


def run(args) -> dict:
    token = Path(args.token_file).read_text().strip()
    items = load(BENCH / f"{args.set}.json")
    if args.only:
        items = [q for q in items if q["id"] in set(args.only.split(","))]
    if args.limit:
        items = items[: args.limit]
    if args.privacy == "default":
        est = len(items) * args.runs * EST_TOKENS_PER_CLOUD_QUESTION
        print(f"cloud allowed: estimated ~{est:,} tokens of the daily Groq budget", flush=True)
        if len(items) * args.runs > args.max_cloud:
            sys.exit(
                f"refusing: {len(items) * args.runs} cloud requests > --max-cloud {args.max_cloud}"
            )
    client = httpx.Client(
        base_url=args.url, headers={"Authorization": f"Bearer {token}"}, timeout=args.timeout
    )
    records = []
    for q in items:
        for r in range(args.runs):
            t0 = time.perf_counter()
            for attempt in range(args.retries + 1):
                if args.pace and attempt + len(records) > 0:
                    time.sleep(args.pace)  # stay under the provider's per-minute token cap
                try:
                    resp = client.post(
                        "/v1/chat",
                        json={"message": args.prefix + q["question"], "privacy": args.privacy},
                    )
                    body = resp.json()
                    reply = body.get("reply", "") if resp.status_code == 200 else ""
                    engine = (
                        (body.get("engine") or {}).get("tier") if resp.status_code == 200 else None
                    )
                    err = None if resp.status_code == 200 else f"http_{resp.status_code}"
                    if engine and body.get("conversation_id"):
                        client.delete(f"/v1/conversations/{body['conversation_id']}")
                except httpx.HTTPError as exc:
                    reply, engine, err = "", None, type(exc).__name__
                if not args.require_engine or engine == args.require_engine:
                    break  # answered by the engine under test
            dt = time.perf_counter() - t0
            wrong_engine = bool(args.require_engine) and engine != args.require_engine
            if wrong_engine:  # a fallback answer must never be scored as the engine under test
                err = err or f"answered_by_{engine}"
            ok = bool(reply) and not wrong_engine and check(q["check"], reply)
            records.append({"id": q["id"], "category": q["category"], "run": r, "ok": ok,
                            "engine": engine, "seconds": round(dt, 2), "error": err, "reply": reply})  # fmt: skip
            print(
                f"  {q['id']} {'PASS' if ok else 'FAIL'} {engine or '-':5s} {dt:5.1f}s  {reply[:70]!r}",
                flush=True,
            )
    return {"label": args.label, "set": args.set, "privacy": args.privacy, "records": records}


def summarise(result: dict) -> None:
    recs = result["records"]
    by_cat: dict[str, list[bool]] = defaultdict(list)
    for r in recs:
        by_cat[r["category"]].append(r["ok"])
    print(f"\n== {result['label']}  set={result['set']}  privacy={result['privacy']}")
    for cat, oks in by_cat.items():
        print(f"  {cat:12s} {sum(oks):2d}/{len(oks):2d}")
    total = sum(r["ok"] for r in recs)
    engines = defaultdict(int)
    for r in recs:
        engines[r["engine"] or "error"] += 1
    secs = [r["seconds"] for r in recs]
    print(f"  {'TOTAL':12s} {total:2d}/{len(recs):2d}  = {100 * total / len(recs):.0f}%")
    print(
        f"  engines: {dict(engines)}  | median {statistics.median(secs):.1f}s, max {max(secs):.1f}s"
    )
    errs = [r for r in recs if r["error"]]
    if errs:
        print(f"  ERRORS: {len(errs)} (these count as failures)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--token-file", required=True)
    ap.add_argument(
        "--set", choices=("dev", "held_out", "hard_dev", "hard_held", "hard_fresh"), default="dev"
    )
    ap.add_argument("--privacy", choices=("local_only", "default"), default="local_only")
    ap.add_argument("--label", default="run")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--max-cloud", type=int, default=60)
    ap.add_argument("--require-engine", choices=("cloud", "local"), default="",
                    help="count an answer only if this tier produced it; retry otherwise")  # fmt: skip
    ap.add_argument("--pace", type=float, default=0, help="seconds to wait before each request")
    ap.add_argument("--retries", type=int, default=0)
    ap.add_argument("--prefix", default="", help="text placed before every question")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    result = run(args)
    summarise(result)
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1))
        print("wrote", args.out)


if __name__ == "__main__":
    main()
