# Making ULTRON more intelligent: what was measured and what was built

Hardware reality: a 4 GB GTX 1650 and a free cloud allowance of about 200K tokens/day. A bigger local model
is not possible, so the levers are *how* the models are used.

## What was built

1. **A private reasoning pass** (`app/core/reasoning.py`, `ULTRON_REASONING=auto`, default on).
   For problem-like messages (arithmetic, dates, combinatorics, logic puzzles, code tracing, counting,
   exact-format writing) the model first solves the problem step by step and checks it; the reply is then
   written with that working. Ordinary conversation is not meant to trigger it (0 false triggers on 26 ordinary
   messages in the final check, 20 of them pinned in tests; a bigger sample could still find some). The solving pass goes through the normal router, so a `local_only` chat is solved on
   the device, and under the `auto` policy it runs **locally first** even when the reply goes to the cloud.
   The working is not read aloud, not stored (only "it ran, which engine, how long, the answer" is), and is
   returned in the reply's `reasoning` field for transparency. If you interrupt (barge-in, NEW CHAT) during
   the solve, the server still ends the turn cleanly and saves nothing.
2. **Web search** (`app/tools/web.py`): **off unless you configure a provider.** Brave Search API (needs a
   key) or a SearXNG instance you run. What leaves the machine is only the question you just asked, shortened
   to 200 characters, and only for recent-sounding questions or an explicit "search for ...". Never in a
   `local_only` conversation, never if the message looks like it holds a secret, never when
   `ULTRON_ALLOW_CLOUD=false`, capped per day, and every use is reported (query, provider, sources). Results
   are stripped of markup and framed to the model as untrusted data.
3. **A benchmark with code-checked answers** (`backend/bench/`, `scripts/bench_intelligence.py`) so that every
   claim here is a measurement. No model grades another model.

## Measurements

Local model = `qwen3:4b-instruct`; cloud = Groq `qwen/qwen3.8-27b`. Scores are questions correct out of N,
temperature 0.6, so one question of difference is within noise.

| set | what it is | reasoning OFF | reasoning ON |
|---|---|---|---|
| easy DEV (24) | general questions | 22/24 (92%): **at ceiling, useless for measuring gains** | not run |
| hard DEV (20, 3 runs) | the set I tuned on | 15, 16, 16 (mean 78%) | 18, 18, 18 (90%) |
| hard HELD-OUT (20, 3 runs) | not used for tuning prompts (trigger patterns were refined looking at it) | 13, 12, 12 (mean 62%) | 20, 20, 20 (100%) |
| hard FRESH (16, 2 runs) | written after everything was frozen | 10, 10 (62%) | **14, 14 (88%)** |

**The honest headline is the FRESH row: about 62% to 88%** on problem-type questions. The 100% on the held-out
set is flattered by the trigger refinement and should not be quoted. Replies were read, not only scored: the
OFF failures were real arithmetic slips (17% of 2,350 = "400.5"; 1,234 x 5,678 = "6,999,952"), a miscounted
letter, a wrong string slice, and ignoring "answer with just the name"; the ON replies fixed them.
Remaining failures with it ON: an addition slip (25+31+31+30+20 written as 87) and a three-person logic puzzle.

Cost: on problem questions the median reply time went from 2.5 s to 12-16 s (two model calls); ordinary
chat is unchanged. Spoken problem answers are therefore slower; `ULTRON_REASONING_VOICE=false` turns the pass
off for voice only.

### Alternatives measured and not adopted

| option (hard DEV unless noted) | result | why not |
|---|---|---|
| cloud 27B answering as configured | 14/20 (70%) | answers in one line without working; no better than local |
| cloud 27B **with** the reasoning pass (held-out) | 16/20 (80%); 2 requests fell back to local and count as misses | local+reasoning scored 20/20 for free, privately, ~2.5x faster; cloud needs pacing under the per-minute cap |
| local "thinking" model `qwen3:4b` | 15/20 with **4 empty replies**, median 34 s | most likely its reasoning runs out of the 4096-token context / 2048-token output limit (not confirmed directly); more context would spill onto the CPU on this card |
| visible "work step by step" in the prompt | mean 16.7 vs 15.7 (3 runs each) | small and noisy; the hidden pass reached 18.0 and also keeps voice replies short |
| removing the embedding model to speed chat | no change (780 ms vs 776 ms) | my suspicion was wrong: the partial CPU offload is the 4B chat model itself on a 4 GB card |

## What I got wrong on the way (so you can weigh the numbers)

Four bugs in my own scoring were found by reading actual replies, and each had inflated or deflated a score:
a "contains the word" check passed "Jupiter has the most moons... Saturn follows" for a question whose answer
is Saturn; digits-only matching failed "three"; "last number wins" failed replies that lead with the answer;
and reading only the "Answer:" line failed correct explanations. All are fixed and unit-tested (57 checker
tests, including a known-good reply for every question). A cloud benchmark run silently fell back to the local
model after 4 requests because of the provider's per-minute cap; the runner now paces itself and refuses to
score an answer from the wrong engine.

## Limits and not verified

- The sets are small (16-24 questions), written by me. Differences of one or two questions are noise.
- Not measured: whether the pass changes ordinary-chat quality (it does not run there), or tone on problem
  answers (the style guard still applies to the final reply).
- It cannot fix knowledge that is stale or missing (the local model still believes Jupiter has the most moons);
  that is what web search is for.
- **Web search has not been run against a real provider**: there is no key on this machine. It is verified
  with a mocked HTTP transport and through the whole app with a fake provider (62 tests, 9 deliberate breaks
  all caught). The Brave documentation I could read confirmed the endpoint, the `X-Subscription-Token`
  header, `q`/`count`, and `web.results[].title/url/description`; it did not state free-tier or payment terms,
  and the result-date field is unconfirmed, so nothing relies on it.
- Not built: code execution or general tool calling. "What does this Python print?" is now answered by
  reasoning, not by running it; running code needs the Phase 6 permission design.

## Run it

```bash
cd backend
PYTHONPATH=. .venv/bin/python scripts/bench_intelligence.py --url http://127.0.0.1:8000 \
    --token-file TOKENFILE --set hard_fresh --privacy local_only --runs 2 --label mytest
```

`--privacy default` lets the router use the cloud and spends tokens (it prints an estimate and caps requests;
add `--pace 40 --require-engine cloud`). To compare, start a second backend with `ULTRON_REASONING=off`.
Do not tune against `hard_held` or `hard_fresh`; write a new set instead.
