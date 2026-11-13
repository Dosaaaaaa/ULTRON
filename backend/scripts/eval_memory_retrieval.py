"""Measure retrieval quality of the real embedding model on a small labelled set.

The data is a fictional operator, not real user data. Questions are paraphrases that share few
words with the memory they should find; the irrelevant questions should find nothing. Used to pick
ULTRON_MEMORY_MIN_SIMILARITY. A threshold tuned on this set is only as good as this set: re-check
on different data (see HELD_OUT) before trusting it.

    PYTHONPATH=. .venv/bin/python scripts/eval_memory_retrieval.py [MODEL] [tune|held-out]
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time

import httpx
import numpy as np

from app.memory.embedder import OllamaEmbedder

MEMORIES = [
    "The operator prefers metric units for all measurements.",
    "The operator is allergic to peanuts.",
    "The operator's sister is called Asha and lives in Leeds.",
    "The operator's main programming language is Python.",
    "The operator wakes at 6 am and trains before work.",
    "The operator drives a green electric hatchback.",
    "The ULTRON project's Phase 4 adds long-term memory with pgvector.",
    "The operator dislikes meetings before 10 am.",
    "The operator's favourite tea is jasmine.",
    "The operator's cat is named Biscuit.",
    "The operator's passport expires in March 2028.",
    "The operator is learning Norwegian for a trip to Bergen in June.",
    "The operator's laptop has a 4 GB NVIDIA GPU.",
    "Deployment of the scoring service happens on Thursdays.",
    "The operator prefers concise answers without apologies.",
]
# (question, index of the memory it should find)
RELEVANT = [
    ("Should I give distances in miles or kilometres?", 0),
    ("Can I order the satay noodles for them?", 1),
    ("Who should I send the birthday card to in Yorkshire?", 2),
    ("Write a script to parse this CSV file", 3),
    ("What time should I schedule the gym session?", 4),
    ("How do I charge the car at home?", 5),
    ("What does the next development stage of ULTRON include?", 6),
    ("Book a call for 9 am tomorrow", 7),
    ("Which hot drink should I buy for the guest?", 8),
    ("Does the pet need feeding while I am away?", 9),
    ("When do I need to renew my travel document?", 10),
    ("Which language should I practise on the flight to Norway?", 11),
    ("Can I run a 7 billion parameter model on my machine?", 12),
    ("When can we ship the new model to production?", 13),
    ("How long should your answers be?", 14),
]
IRRELEVANT = [
    "What is the capital of Australia?",
    "Explain the Pythagorean theorem.",
    "Tell me a joke about penguins.",
    "How does photosynthesis work?",
    "Who wrote War and Peace?",
    "What is 17 times 23?",
    "Summarise the plot of Hamlet.",
    "Translate good morning into French.",
    "How many moons does Mars have?",
    "Give me a recipe for pancakes.",
]

# A second, different set: used only to check that floors picked on the first one generalise.
HELD_OUT_MEMORIES = [
    "The weekly planning meeting is on Mondays at 9:30.",
    "The operator is vegetarian but eats eggs.",
    "The staging database password rotation is handled by the platform team.",
    "The operator's flight to Lisbon departs on 14 March.",
    "The operator uses a standing desk and a vertical mouse because of wrist pain.",
    "The team's release branch is called stable and is protected.",
    "The operator's dentist is Dr Okafor, appointments on Tuesdays.",
    "The operator is saving for a house deposit by the end of next year.",
    "The operator runs a 10 km loop along the canal on Saturdays.",
    "The client Northwind wants weekly status reports in PDF form.",
    "The operator's daughter starts school in September.",
    "The operator prefers dark mode in every application.",
]
HELD_OUT_RELEVANT = [
    ("When should I block time for sprint planning?", 0),
    ("Suggest something for dinner that fits what I eat", 1),
    ("Who looks after changing the credentials on the test database?", 2),
    ("What day do I need to be at the airport?", 3),
    ("Recommend an ergonomic keyboard setup", 4),
    ("Which branch should I merge the hotfix into?", 5),
    ("When is my teeth check-up?", 6),
    ("Can I afford this expensive laptop right now?", 7),
    ("Plan my weekend training", 8),
    ("How often do I owe Northwind an update and in what format?", 9),
    ("What should we prepare for the autumn term?", 10),
    ("Which theme should the new app default to?", 11),
]
HELD_OUT_IRRELEVANT = [
    "What is the boiling point of water at altitude?",
    "Who painted the Mona Lisa?",
    "Explain how a binary search works.",
    "What are the symptoms of dehydration?",
    "Name three moons of Jupiter.",
    "Convert 50 miles to kilometres.",
    "Write a haiku about autumn.",
    "What does HTTP status 404 mean?",
]


async def run(model: str, prefixes: bool, held_out: bool = False) -> dict:
    global MEMORIES, RELEVANT, IRRELEVANT
    if held_out:
        MEMORIES, RELEVANT, IRRELEVANT = HELD_OUT_MEMORIES, HELD_OUT_RELEVANT, HELD_OUT_IRRELEVANT
    async with httpx.AsyncClient() as client:
        emb = OllamaEmbedder(client, "http://127.0.0.1:11434", model, timeout_s=30)
        if not prefixes:
            emb._doc_prefix = emb._query_prefix = ""
        await emb.embed(["warm up"])  # load the model before timing
        t0 = time.perf_counter()
        mem = np.asarray(await emb.embed(MEMORIES), dtype=np.float32)
        doc_ms = (time.perf_counter() - t0) * 1000
        lat = []
        qs = []
        for q, _ in RELEVANT:
            t = time.perf_counter()
            qs.append((await emb.embed([q], query=True))[0])
            lat.append((time.perf_counter() - t) * 1000)
        irr = [(await emb.embed([q], query=True))[0] for q in IRRELEVANT]
    rel_sims = np.asarray(qs, dtype=np.float32) @ mem.T
    irr_sims = np.asarray(irr, dtype=np.float32) @ mem.T
    ranks = [int((rel_sims[i] > rel_sims[i, t]).sum()) + 1 for i, (_, t) in enumerate(RELEVANT)]
    target = [float(rel_sims[i, t]) for i, (_, t) in enumerate(RELEVANT)]
    return {
        "dim": mem.shape[1],
        "prefixes": prefixes,
        "recall@1": sum(r == 1 for r in ranks) / len(ranks),
        "recall@3": sum(r <= 3 for r in ranks) / len(ranks),
        "target_sim_min/median": (min(target), statistics.median(target)),
        "irrelevant_max_sim_max/median": (float(irr_sims.max()), float(np.median(irr_sims.max(1)))),
        "query_ms_median": statistics.median(lat),
        "batch15_docs_ms": doc_ms,
        "rel": rel_sims,
        "irr": irr_sims,
    }


def sweep(res: dict) -> None:
    rel, irr = res["rel"], res["irr"]
    print(
        "  floor | relevant found (target in top-5 above floor) | irrelevant queries with any hit"
    )
    for floor in (0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70):
        found = 0
        for i, (_, t) in enumerate(RELEVANT):
            top = np.argsort(-rel[i])[:5]
            found += int(t in top and rel[i, t] >= floor)
        fp = int((irr.max(1) >= floor).sum())
        print(
            f"  {floor:.2f}  | {found:2d}/{len(RELEVANT)}".ljust(60)
            + f"| {fp:2d}/{len(IRRELEVANT)}"
        )


async def main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else "nomic-embed-text"
    which = sys.argv[2] if len(sys.argv) > 2 else "tune"
    for prefixes in (True, False):
        res = await run(model, prefixes, held_out=which == "held-out")
        print(
            f"\n== {model} [{which}] task prefixes={'on' if prefixes else 'off'} dim={res['dim']}"
        )
        for k, v in res.items():
            if k not in ("rel", "irr", "dim", "prefixes"):
                print(f"  {k}: {v if not isinstance(v, tuple) else tuple(round(x, 3) for x in v)}")
        sweep(res)


if __name__ == "__main__":
    asyncio.run(main())
