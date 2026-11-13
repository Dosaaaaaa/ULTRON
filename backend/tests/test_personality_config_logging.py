from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.logging import JsonFormatter, redact, register_secrets
from app.core.personality import PersonalityEngine, load_personality
from app.models.router import PromptContext
from tests.conftest import PROMPTS, TOKEN

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def ctx(**kw):
    base = dict(tier="cloud", provider="p", model="m", degraded=False, local_only=False)
    base.update(kw)
    return PromptContext(**base)


# ---- personality -----------------------------------------------------------
def test_personality_loads_versioned_file_with_hash():
    p = load_personality(PROMPTS, 1)
    assert p.version == 1 and "ULTRON" in p.text and len(p.sha256) == 64


def test_missing_or_empty_personality_version_fails_loudly(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_personality(tmp_path, 9)
    (tmp_path / "ultron.v2.md").write_text("   \n")
    with pytest.raises(ValueError):
        load_personality(tmp_path, 2)


def test_personality_is_identical_across_providers_only_runtime_block_differs():
    eng = PersonalityEngine(load_personality(PROMPTS, 1))
    cloud = eng.system_prompt(ctx(tier="cloud", model="big"), NOW)
    local = eng.system_prompt(ctx(tier="local", provider="ollama", model="qwen3:4b"), NOW)
    text = eng.personality.text
    assert cloud.startswith(text) and local.startswith(text)
    assert "small local model" in local and "small local model" not in cloud


def test_runtime_context_states_real_limits_and_degradation():
    eng = PersonalityEngine(load_personality(PROMPTS, 1))
    p = eng.system_prompt(ctx(tier="local", degraded=True, local_only=True), NOW)
    assert "NO tools" in p and "NO memory beyond" in p
    assert "entirely on the operator's own device" in p
    assert "more capable engine was preferred" in p
    assert "2026-11-10T12:00" in p


# ---- config ----------------------------------------------------------------
def make(**kw):
    return Settings(_env_file=None, **kw)


def test_token_is_required():
    with pytest.raises(ValidationError, match="ULTRON_API_TOKEN is required"):
        make()


def test_short_token_rejected():
    with pytest.raises(ValidationError, match="at least"):
        make(api_token="short")


def test_blank_token_treated_as_missing():
    with pytest.raises(ValidationError, match="required"):
        make(api_token="   ")


def test_auth_can_only_be_disabled_on_loopback():
    assert make(auth_disabled=True).auth_disabled
    with pytest.raises(ValidationError, match="loopback"):
        make(auth_disabled=True, host="0.0.0.0")  # noqa: S104


def test_secrets_not_in_repr_and_cloud_configured_flag():
    s = make(api_token=TOKEN, cloud_api_key="k" * 30, cloud_model="m")
    assert TOKEN not in repr(s) and "k" * 30 not in repr(s)
    assert s.cloud_configured
    assert not make(api_token=TOKEN, cloud_api_key="k" * 30).cloud_configured  # no model


# ---- logging ---------------------------------------------------------------
def test_redaction_patterns():
    assert "abcdef1234567890" not in redact("Authorization: Bearer abcdef1234567890")
    assert "sk-" not in redact("key sk-abcdefghijklmnopqrstuv here")
    assert "AIza" not in redact("AIzaSyA1234567890abcdefghijkl")
    assert "hunter22222" not in redact('{"api_key": "hunter22222"}')
    assert redact("plain text stays") == "plain text stays"


def test_registered_secret_is_redacted_anywhere():
    register_secrets(["ZZ-very-custom-secret-value"])
    assert "ZZ-very" not in redact("oops ZZ-very-custom-secret-value leaked")


def test_json_formatter_redacts_extras_and_messages():
    register_secrets([TOKEN])
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "token=%s", (TOKEN,), None)
    rec.detail = f"auth {TOKEN}"
    line = JsonFormatter().format(rec)
    assert TOKEN not in line
    assert json.loads(line)["level"] == "INFO"


# Rules every rendering of the personality must state (compared case-insensitively). The compact
# cloud text and the longer local text are different wordings of the SAME rules.
CORE_RULES = (  # each rule: the wordings the two renderings use
    ("no emoji",),
    ("apologise",),
    ("no exclamation marks",),
    ('say "no." in plain words', 'start with the plain word "no."'),
    ("symbol, emoji or capitals to mark the refusal", "no symbol or emoji"),
    ("you can say", "what you can say"),  # limits concern acting/knowing, never what it can SAY
    ("can you help me with x?",),
    ("never introduce yourself as that model",),
    ("never state what other people know, think, feel or intend",
     "never state what other people know, think or feel"),
    ("runtime context is authoritative",),
    ("never reuse",),
)  # fmt: skip


def test_default_personality_is_v4_and_both_renderings_carry_the_core_rules():
    s = make(api_token=TOKEN)
    assert s.personality_version == 4 and s.ollama_model == "qwen3:4b-instruct"
    assert s.ollama_think is False  # instruct model: no separate reasoning channel
    p = load_personality(PROMPTS, s.personality_version)
    assert p.local_text and p.local_sha256 and p.sha256 != p.local_sha256
    for name, text in (("default", p.text), ("local", p.local_text)):
        missing = [r[0] for r in CORE_RULES if not any(w in text.lower() for w in r)]
        assert not missing, f"{name} rendering lost rules: {missing}"
    assert len(p.text.split()) < len(p.local_text.split())  # the cloud text is the compact one
    assert len({load_personality(PROMPTS, v).sha256 for v in (1, 2, 3, 4)}) == 4


def test_tier_selects_the_rendering_and_versions_without_one_are_unchanged(tmp_path: Path):
    (tmp_path / "ultron.v1.md").write_text("DEFAULT TEXT")
    (tmp_path / "ultron.v1.local.md").write_text("LOCAL TEXT")
    (tmp_path / "ultron.v2.md").write_text("ONLY DEFAULT")
    eng1 = PersonalityEngine(load_personality(tmp_path, 1))
    assert eng1.system_prompt(ctx(tier="cloud"), NOW).startswith("DEFAULT TEXT")
    assert eng1.system_prompt(ctx(tier="local"), NOW).startswith("LOCAL TEXT")
    eng2 = PersonalityEngine(load_personality(tmp_path, 2))
    assert eng2.system_prompt(ctx(tier="local"), NOW).startswith("ONLY DEFAULT")
    assert load_personality(tmp_path, 2).local_sha256 is None
    (tmp_path / "ultron.v3.md").write_text("X")
    (tmp_path / "ultron.v3.local.md").write_text("  \n")
    with pytest.raises(ValueError):
        load_personality(tmp_path, 3)
