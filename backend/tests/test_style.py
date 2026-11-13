"""'No emoji, no exclamation marks' are enforced in code for every engine and every transport."""

from __future__ import annotations

import itertools
import random

import pytest

from app.core.style import StyleGuard, apply_style
from app.models.base import StreamChunk
from app.models.router import ModelRouter, RoutedResponse, StreamStarted
from tests.fakes import FakeAdapter, StreamingFake


# ---------------------------------------------------------------------- pure rules
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Hello!", "Hello."),
        ("Done!!!", "Done."),
        ("Hello! How are you? Fine!", "Hello. How are you? Fine."),
        ("No. 🫠", "No. "),
        ("Rocket 🚀 launched ✅", "Rocket  launched "),
        ("flags 🇯🇵 and 👍🏽 and ❤️", "flags  and  and "),
        ("Wow ! really", "Wow  really"),  # '!' after a space is simply dropped
        ("Stop.!", "Stop."),
        ("Really?!", "Really?"),
        ("x != y", "x != y"),  # code survives
        ("color: red !important;", "color: red !important;"),
        ("![diagram](a.png)", "![diagram](a.png)"),
        ("if (!ok) return;", "if (!ok) return;"),
        ('She said "go!" and left', 'She said "go." and left'),
        ("(really!)", "(really.)"),
        ("temp 20° → 25°, ™ © € ok", "temp 20° → 25°, ™ © € ok"),  # ordinary symbols kept
        ("naïve café 日本語 Привет", "naïve café 日本語 Привет"),
        ("", ""),
        ("!", ""),
        ("plain text.", "plain text."),
    ],
)
def test_apply_style_cases(raw, expected):
    assert apply_style(raw) == expected


def test_output_never_contains_emoji_or_bang_runs_at_sentence_ends():
    rng = random.Random(1)
    alphabet = ["Hi", " ", "!", "!!", "🙂", "👍🏽", "✅", ".", "?", "\n", '"', ")", "x=", "!="]
    for _ in range(400):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 14)))
        out = apply_style(text)
        assert not any(ch in out for ch in "🙂👍✅"), (text, out)
        assert "!\n" not in out and not out.endswith("!") and "! " not in out, (text, out)


# ---------------------------------------------------------------------- streaming invariance
def feed_all(chunks):
    g = StyleGuard()
    return "".join(g.feed(c) for c in chunks) + g.flush()


def all_splits(text, max_parts=4):
    n = len(text)
    for k in range(0, max_parts):
        for cuts in itertools.combinations(range(1, n), k):
            pts = (0, *cuts, n)
            yield [text[a:b] for a, b in itertools.pairwise(pts)]


@pytest.mark.parametrize(
    "text",
    [
        "Hi! Yes!! 🙂 ok",
        "a !b !!",
        "x != y! z",
        '"go!" (now!)',
        "end!",
        "🙂!",
        "Done.! ok?! yes",
        "!!! start",
        "tab\t! x\n! y",
    ],
)
def test_every_chunking_gives_the_same_output_as_the_whole_text(text):
    whole = apply_style(text)
    for chunks in all_splits(text):
        assert feed_all(chunks) == whole, (chunks, whole)


def test_random_chunkings_of_longer_text_match():
    rng = random.Random(7)
    pieces = ["Hello", "!", "!!", " ", "🙂", "✅", ".", "?", "\n", "x != y", "(ok!)", "word"]
    for _ in range(300):
        text = "".join(rng.choice(pieces) for _ in range(rng.randint(5, 30)))
        cuts = sorted(rng.sample(range(1, len(text)), min(len(text) - 1, rng.randint(1, 8))))
        pts = (0, *cuts, len(text))
        chunks = [text[a:b] for a, b in itertools.pairwise(pts)]
        assert feed_all(chunks) == apply_style(text), (chunks,)


# ---------------------------------------------------------------------- router integration
class FakeWithText(FakeAdapter):
    def __init__(self, tier, text, **kw):
        super().__init__(tier, **kw)
        self._text = text

    async def complete(self, request):
        r = await super().complete(request)
        return type(r)(**{**r.__dict__, "content": self._text})


def router_for(adapter, **kw):
    return ModelRouter(cloud=None, local=adapter, policy="local_only", **kw)


async def run_complete(router):
    return await router.complete([], build_system_prompt=lambda ctx: "sys")


async def test_complete_is_sanitised_for_every_engine():
    for tier in ("local", "cloud"):
        a = FakeWithText(tier, "No. Cheerfulness would be noise! 🚀✨")
        r = ModelRouter(
            cloud=a if tier == "cloud" else None,
            local=a if tier == "local" else None,
            policy="cloud_first" if tier == "cloud" else "local_only",
        )
        out = await run_complete(r)
        assert out.response.content == "No. Cheerfulness would be noise. "


async def test_emoji_only_reply_becomes_a_plain_refusal():
    out = await run_complete(router_for(FakeWithText("local", "🫠")))
    assert out.response.content == "No."


async def test_enforcement_can_be_switched_off():
    out = await run_complete(router_for(FakeWithText("local", "Hi! 🙂"), enforce_style=False))
    assert out.response.content == "Hi! 🙂"


async def collect(router):
    events = [e async for e in router.stream([], build_system_prompt=lambda ctx: "sys")]
    chunks = [e.text for e in events if isinstance(e, StreamChunk)]
    final = next(e for e in events if isinstance(e, RoutedResponse))
    started = [e for e in events if isinstance(e, StreamStarted)]
    return chunks, final, started


@pytest.mark.parametrize(
    "chunks",
    [
        ("Hello", "!", " there ", "🙂", "! Done!"),
        ("Hi ", "🚀", "✨"),
        ("a", "!", "!", "=b ", "!"),
    ],
)
async def test_stream_deltas_and_final_reply_agree_and_are_clean(chunks):
    r = router_for(StreamingFake("local", chunks=chunks))
    deltas, final, started = await collect(r)
    assert len(started) == 1
    assert "".join(deltas).strip() == final.response.content == apply_style("".join(chunks)).strip()
    assert all(d for d in deltas)  # no empty delta is ever sent
    assert not any(ch in final.response.content for ch in "🙂🚀✨")


async def test_stream_of_only_emoji_says_no_and_start_follows_first_visible_text():
    r = router_for(StreamingFake("local", chunks=("🙂", "🫠", "!")))
    deltas, final, started = await collect(r)
    assert deltas == ["No."] and final.response.content == "No." and len(started) == 1


async def test_fallback_still_possible_when_first_engine_emitted_only_invisible_text():
    """Nothing visible was sent, so the router may still fall back to the next engine."""
    from app.models.base import ProviderUnavailable

    bad = StreamingFake("cloud", chunks=("🙂", "x"), fail_at=1, error=ProviderUnavailable("down"))
    good = StreamingFake("local", chunks=("All ", "good."))
    r = ModelRouter(cloud=bad, local=good, policy="cloud_first")
    deltas, final, started = await collect(r)
    assert "".join(deltas) == "All good." and started[0].tier == "local" and final.route.fallback


def test_exhaustive_small_alphabet_every_chunking_matches():
    alphabet = ["a", "!", " ", ".", "\n", "🙂"]
    for n in range(1, 6):
        for tup in itertools.product(alphabet, repeat=n):
            text = "".join(tup)
            whole = apply_style(text)
            for chunks in all_splits(text, max_parts=4):
                assert feed_all(chunks) == whole, (chunks, whole)
