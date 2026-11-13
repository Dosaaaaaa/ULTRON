"""Long conversations must not cost more and more tokens per turn."""

from __future__ import annotations

from app.core.orchestrator import trim_history
from app.models.base import ChatMessage
from tests.conftest import AUTH
from tests.fakes import FakeAdapter


def turns(n: int, size: int = 100) -> list[ChatMessage]:
    out = []
    for i in range(n):
        out.append(ChatMessage("user", f"u{i} " + "x" * size))
        out.append(ChatMessage("assistant", f"a{i} " + "y" * size))
    return out


def test_everything_is_kept_when_it_fits():
    h = turns(3)
    assert trim_history(h, 100_000) == h


def test_the_oldest_turns_go_first_and_the_window_starts_on_a_user_message():
    h = turns(20)
    kept = trim_history(h, 1000)
    assert kept[-1] == h[-1] and kept[0].role == "user"
    assert sum(len(m.content) for m in kept) <= 1000 + 200  # budget, plus the rounding to a turn
    assert len(kept) < len(h) and kept == h[-len(kept) :]  # a suffix: order and content untouched


def test_the_last_exchange_is_always_kept_even_if_it_alone_is_over_budget():
    h = turns(5, size=5000)
    kept = trim_history(h, 500)
    assert [m.role for m in kept] == ["user", "assistant"] and kept[-1] == h[-1]


def test_empty_and_single_message_histories_are_safe():
    assert trim_history([], 500) == []
    one = [ChatMessage("user", "hi")]
    assert trim_history(one, 1) == one


async def test_the_prompt_stays_small_in_a_long_conversation_but_nothing_stored_is_lost(run_app):
    local = FakeAdapter("local", reply="r" * 400)
    async with run_app(None, local, history_char_budget=1500) as (c, _):
        cid = None
        sizes = []
        for i in range(25):
            r = await c.post(
                "/v1/chat",
                json={"message": f"question {i} " + "q" * 400, "conversation_id": cid},
                headers=AUTH,
            )
            cid = r.json()["conversation_id"]
            sizes.append(sum(len(m.content) for m in local.requests[-1].messages))
        stored = (await c.get(f"/v1/conversations/{cid}", headers=AUTH)).json()["messages"]
    assert len(stored) == 50  # the stored chat is complete
    # Without the budget the last prompt would carry 20 earlier messages (about 8000 characters more
    # than the first prompt). With it, the growth is about the budget plus one turn.
    assert sizes[-1] - sizes[0] < 3000
