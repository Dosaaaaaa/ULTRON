"""The private reasoning pass: which messages trigger it, what it sends, what it may never do."""

from __future__ import annotations

import asyncio

import pytest

from app.core.reasoning import SOLVER_SYSTEM, needs_reasoning, parse_working
from app.models.base import ModelResponse, ProviderUnavailable
from tests.conftest import AUTH
from tests.fakes import FakeAdapter, StreamingFake

PROBLEMS = [
    "What is 487 multiplied by 36?",
    "What is 23% of 1,840?",
    "What day of the week will it be 100 days after a Wednesday?",
    "How many days are there from March 3 to July 19 in a non-leap year?",
    "In how many distinct ways can the letters of the word LEVEL be arranged?",
    "What is the probability of getting exactly two heads when flipping a fair coin three times?",
    "A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the ball. How many cents does the ball cost?",
    "What number comes next in the sequence 2, 6, 12, 20, 30?",
    "What does this Python print?\n```python\nprint(sum(range(10)))\n```",
    "How many times does the letter 'r' appear in the word 'strawberry'?",
    "Write a sentence of exactly seven words whose third word is 'blue'.",
    "Alice is taller than Ben. Ben is taller than Cara. Who is the tallest?",
    "Convert 72 kilometres per hour to metres per second.",
    "Solve 3x + 5 = 20",
    "How many handshakes happen if 12 people each shake hands once?",
]
CHAT = [
    "Hello there",
    "Tell me a joke",
    "Who are you?",
    "What is the capital of Australia?",
    "Remember that I prefer metric units",
    "Which planet has the most moons?",
    "Summarise the plot of Hamlet",
    "What's the weather like today?",
    "Explain how a heat pump works",
    "How many people live in Tokyo?",
    "How much does a Tesla cost?",
    "Give me a combination of colours that goes with navy",
    "What is the probability that you are right?",
    "How many ways are there to cook an egg?",
    "What is the best arrangement of furniture for a small living room?",
    "Tell me about the percent of Earth covered by water",
    "Thanks, that helps",
    "Why did Einstein win the Nobel Prize for relativity?",
    "Write a haiku about autumn",
    "Stop",
]


@pytest.mark.parametrize("text", PROBLEMS)
def test_problems_trigger_the_reasoning_pass(text):
    assert needs_reasoning(text), text


@pytest.mark.parametrize("text", CHAT)
def test_ordinary_conversation_does_not(text):
    assert not needs_reasoning(text), text


def test_extreme_lengths_never_trigger():
    assert not needs_reasoning("hi")
    assert not needs_reasoning("what is 2 + 2? " * 400)  # a pasted wall of text is not a problem


def test_parse_working():
    w = parse_working("Step 1: 6 * 7.\nStep 2: check.\nAnswer: 42", "local")
    assert w.answer == "42" and w.tier == "local" and "Step 1" in w.text
    assert parse_working("no marker here", "cloud").answer is None
    assert parse_working("   ", "local") is None
    assert len(parse_working("x" * 5000, "local").text) == 1800
    note = w.note()
    assert "Private working" in note and "Do not read the working out" in note
    assert "Best answer from the working: 42" in note


class Recording(FakeAdapter):
    """Answers the solver with a worked solution and everything else with `reply`."""

    def __init__(self, tier, *, solver="Work: 6 * 7 = 42.\nAnswer: 42", **kw):
        super().__init__(tier, **kw)
        self.solver = solver

    def is_solver(self, request) -> bool:
        return request.messages[0].content == SOLVER_SYSTEM

    async def complete(self, request):
        if self.error:
            self.requests.append(request)
            raise self.error
        if self.is_solver(request):
            self.requests.append(request)
            return ModelResponse(self.solver, self.provider, self.model, self.tier, 5, 10, 10)
        return await super().complete(request)


def solver_calls(a):
    return [r for r in a.requests if r.messages[0].content == SOLVER_SYSTEM]


def final_calls(a):
    return [r for r in a.requests if r.messages[0].content != SOLVER_SYSTEM]


async def chat(c, message, **kw):
    r = await c.post("/v1/chat", json={"message": message, **kw}, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


async def test_a_problem_is_solved_privately_first_and_the_reply_is_written_with_the_working(
    run_app,
):
    local = Recording("local", reply="Forty-two.")
    async with run_app(None, local) as (c, _):
        out = await chat(c, "What is 6 multiplied by 7?")
    assert len(solver_calls(local)) == 1 and len(final_calls(local)) == 1
    final_prompt = final_calls(local)[0].messages[0].content
    assert "Private working" in final_prompt and "Best answer from the working: 42" in final_prompt
    assert out["reply"] == "Forty-two."  # the working is not the reply
    r = out["reasoning"]
    assert (
        r["used"] is True
        and r["engine"] == "local"
        and r["answer"] == "42"
        and "6 * 7" in r["working"]
    )


async def test_ordinary_chat_costs_no_extra_call(run_app):
    local = Recording("local")
    async with run_app(None, local) as (c, _):
        out = await chat(c, "Tell me a joke")
    assert solver_calls(local) == [] and len(final_calls(local)) == 1
    assert out["reasoning"] == {"used": False, "reason": "not needed", "engine": None, "seconds": None, "answer": None, "working": None}  # fmt: skip


async def test_the_solver_sees_the_question_and_recent_history_not_the_whole_chat(run_app):
    local = Recording("local")
    async with run_app(None, local) as (c, _):
        first = await chat(c, "Tell me a joke")
        for i in range(8):
            await chat(c, f"chatty message {i}", conversation_id=first["conversation_id"])
        await chat(c, "What is 6 multiplied by 7?", conversation_id=first["conversation_id"])
    sent = solver_calls(local)[0].messages
    assert sent[-1].content == "What is 6 multiplied by 7?"
    assert len(sent) == 1 + 7  # the solver system prompt + at most the last 7 messages


async def test_it_can_be_switched_off_or_off_for_voice(run_app):
    local = Recording("local")
    async with run_app(None, local, reasoning="off") as (c, _):
        out = await chat(c, "What is 6 multiplied by 7?")
    assert solver_calls(local) == [] and out["reasoning"]["reason"] == "off"
    local2 = Recording("local")
    async with run_app(None, local2, reasoning_voice=False) as (c, _):
        voice = await chat(c, "What is 6 multiplied by 7?", modality="voice")
        text = await chat(c, "What is 6 multiplied by 7?", modality="text")
    assert (
        voice["reasoning"]["reason"] == "disabled for voice" and text["reasoning"]["used"] is True
    )
    assert len(solver_calls(local2)) == 1


async def test_a_private_conversation_is_solved_on_the_device_and_never_touches_the_cloud(run_app):
    cloud, local = Recording("cloud"), Recording("local", reply="Forty-two.")
    async with run_app(cloud, local, routing_policy="cloud_first") as (c, _):
        out = await chat(c, "What is 6 multiplied by 7?", privacy="local_only")
        locked = await chat(c, "What is 8 multiplied by 9?", conversation_id=out["conversation_id"])
    assert cloud.requests == []  # neither the solving pass nor the reply went to the cloud
    assert out["reasoning"]["engine"] == "local" and locked["reasoning"]["engine"] == "local"


async def test_with_cloud_allowed_the_solver_uses_the_same_engine_policy(run_app):
    cloud, local = Recording("cloud", reply="Forty-two."), Recording("local")
    async with run_app(cloud, local, routing_policy="cloud_first") as (c, _):
        out = await chat(c, "What is 6 multiplied by 7?")
    assert len(solver_calls(cloud)) == 1 and len(final_calls(cloud)) == 1 and local.requests == []
    assert out["reasoning"]["engine"] == "cloud"


async def test_if_the_solver_fails_the_reply_still_comes_and_says_why(run_app):
    cloud = Recording("cloud", error=ProviderUnavailable("down"))
    local = Recording("local", reply="Forty-two.")
    # cloud fails for both passes; the router falls back to local for each
    async with run_app(cloud, local, routing_policy="cloud_first") as (c, _):
        out = await chat(c, "What is 6 multiplied by 7?")
    assert out["reply"] == "Forty-two." and out["engine"]["tier"] == "local"
    assert out["reasoning"]["used"] is True and out["reasoning"]["engine"] == "local"


async def test_when_no_model_can_solve_it_the_conversation_is_not_blocked(run_app):
    class SolverDown(Recording):
        async def complete(self, request):
            if self.is_solver(request):
                raise ProviderUnavailable("down")
            return await FakeAdapter.complete(self, request)

    local = SolverDown("local", reply="Forty-two.")
    async with run_app(None, local) as (c, _):
        out = await chat(c, "What is 6 multiplied by 7?")
    assert out["reply"] == "Forty-two."
    assert out["reasoning"]["used"] is False and "no model" in out["reasoning"]["reason"]


async def test_an_empty_solver_reply_is_ignored(run_app):
    local = Recording("local", solver="   ", reply="Forty-two.")
    async with run_app(None, local) as (c, _):
        out = await chat(c, "What is 6 multiplied by 7?")
    assert out["reasoning"]["used"] is False and "nothing" in out["reasoning"]["reason"]
    assert "Private working" not in final_calls(local)[0].messages[0].content


async def test_the_stored_record_keeps_the_conclusion_but_not_the_working(run_app):
    local = Recording("local", solver="SECRET-SCRATCH-WORK 6*7\nAnswer: 42")
    async with run_app(None, local) as (c, _):
        out = await chat(c, "What is 6 multiplied by 7?")
        msgs = (await c.get(f"/v1/conversations/{out['conversation_id']}", headers=AUTH)).json()[
            "messages"
        ]
    route = msgs[-1]["route"]["reasoning"]
    assert route["used"] is True and route["answer"] == "42" and "working" not in route
    assert "SECRET-SCRATCH-WORK" not in str(msgs)


async def test_the_working_never_appears_in_the_conversation_history(run_app):
    local = Recording("local", solver="HIDDEN-STEP-ONE\nAnswer: 42", reply="Forty-two.")
    async with run_app(None, local) as (c, _):
        out = await chat(c, "What is 6 multiplied by 7?")
        follow = await chat(c, "And 8 multiplied by 9?", conversation_id=out["conversation_id"])
    history = " ".join(m.content for m in final_calls(local)[-1].messages[1:])
    assert "HIDDEN-STEP-ONE" not in history and follow["reply"] == "Forty-two."


# ----------------------------------------------------------------- streaming
def ws_server(tmp_path, local):
    from starlette.testclient import TestClient

    from app.main import create_app
    from tests.conftest import make_settings

    return TestClient(create_app(make_settings(tmp_path), local=local))


class SlowSolver(StreamingFake):
    def is_solver(self, request) -> bool:
        return request.messages[0].content == SOLVER_SYSTEM

    async def complete(self, request):
        if self.is_solver(request):
            await asyncio.sleep(10)
        return await super().complete(request)


def test_websocket_reports_reasoning_in_the_start_and_end_frames(tmp_path):
    from tests.test_ws import URL, recv_until
    from tests.test_ws import chat as ws_chat

    class Fast(StreamingFake):
        async def complete(self, request):
            return ModelResponse("Work.\nAnswer: 42", "p", "m", "local", 1, 1, 1)

    with (
        ws_server(tmp_path, Fast("local")) as client,
        client.websocket_connect(URL, headers=AUTH) as ws,
    ):
        ws_chat(ws, "What is 6 multiplied by 7?")
        events = recv_until(ws)
    start = next(e for e in events if e["type"] == "start")
    assert start["reasoning"]["used"] is True and start["reasoning"]["answer"] == "42"
    assert events[-1]["reasoning"]["used"] is True


def test_interrupting_during_the_private_solve_still_ends_the_turn_and_saves_nothing(tmp_path):
    from tests.test_ws import URL, recv_until
    from tests.test_ws import chat as ws_chat

    with (
        ws_server(tmp_path, SlowSolver("local")) as client,
        client.websocket_connect(URL, headers=AUTH) as ws,
    ):
        ws_chat(ws, "remember that I like tea, and what is 6 multiplied by 7?")
        import time

        time.sleep(0.4)  # the solver is now sleeping
        ws.send_json({"type": "cancel"})
        events = recv_until(ws)
        assert events[-1]["type"] == "end" and events[-1]["interrupted"] is True
        assert events[-1]["message_id"] is None and events[-1]["reply"] == ""
        assert client.get("/v1/conversations", headers=AUTH).json() == []  # nothing was saved
        # and the connection is still usable for the next turn
        ws_chat(ws, "Tell me a joke")
        assert recv_until(ws)[-1]["type"] == "end"


async def test_under_the_auto_policy_the_solver_runs_locally_even_when_the_reply_goes_to_the_cloud(
    run_app,
):
    cloud, local = Recording("cloud", reply="Forty-two."), Recording("local")
    ask = "Explain why 6 multiplied by 7 is what it is, and what is 8 times 9?"  # 'complex' for routing
    async with run_app(cloud, local, routing_policy="auto") as (c, _):
        out = await chat(c, ask)
    assert out["engine"]["tier"] == "cloud"  # the reply itself still goes to the stronger engine
    assert len(solver_calls(local)) == 1 and solver_calls(cloud) == []  # but solving was on-device
    assert out["reasoning"]["engine"] == "local"


async def test_the_solver_working_is_not_rewritten_by_the_style_guard(run_app):
    # Review finding: the "no exclamation mark" rule turned factorials and code literals in the
    # private working into full stops. The working is never shown or spoken, so it stays verbatim.
    local = Recording("local", solver='5! = 120, print("Hello!")\nAnswer: 120', reply="It is 120!")
    async with run_app(None, local) as (c, _):
        out = await chat(c, "What is 5 factorial multiplied by 1?")
    r = out["reasoning"]
    assert r["used"] is True and '5! = 120, print("Hello!")' in r["working"]
    assert out["reply"] == "It is 120."  # the reply the operator reads is still styled


async def test_a_solver_that_returns_only_exclamations_is_not_turned_into_working(run_app):
    local = Recording("local", solver="!!!", reply="Forty-two.")
    async with run_app(None, local) as (c, _):
        out = await chat(c, "What is 6 multiplied by 7?")
    assert out["reasoning"]["used"] is True or "nothing" in out["reasoning"]["reason"]
    assert "No." not in str(out["reasoning"].get("working", ""))


def test_interrupting_while_memory_is_searched_still_ends_the_turn_and_keeps_no_memory(tmp_path):
    # Review finding: a cancel during prepare() (embedder / web search) sent no `end` frame and left
    # the "remember that ..." note stored although the turn saved nothing.
    import time

    from starlette.testclient import TestClient

    from app.main import create_app
    from tests.conftest import make_settings
    from tests.fakes import FakeEmbedder
    from tests.test_ws import URL, recv_until
    from tests.test_ws import chat as ws_chat

    class SlowSearchEmbedder(FakeEmbedder):
        async def embed(self, texts, *, query=False):
            if query:  # retrieval hangs; storing the note itself works
                await asyncio.sleep(10)
            return await super().embed(texts, query=query)

    app = create_app(
        make_settings(tmp_path, memory_enabled=True),
        local=StreamingFake("local"),
        embedder=SlowSearchEmbedder(),
    )
    with TestClient(app) as client, client.websocket_connect(URL, headers=AUTH) as ws:
        ws_chat(ws, "remember that I like green tea")
        time.sleep(0.5)  # the note is stored; the retrieval is now sleeping
        ws.send_json({"type": "cancel"})
        events = recv_until(ws)
        assert events[-1]["type"] == "end" and events[-1]["interrupted"] is True
        assert events[-1]["message_id"] is None
        assert client.get("/v1/memories", headers=AUTH).json()["total"] == 0
        assert client.get("/v1/conversations", headers=AUTH).json() == []
