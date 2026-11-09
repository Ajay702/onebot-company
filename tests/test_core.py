from datetime import datetime
from pathlib import Path

import pytest

from onebot import core
from onebot.llm import DemoClient

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def charters():
    return core.load_charters(ROOT / "company" / "charters")


def test_charter_prompt_contains_approval_line(charters):
    prompt = charters["inbox_manager"].system_prompt()
    assert "You are the Inbox Manager." in prompt
    assert "stop and ask the owner" in prompt
    assert "Quoting any price" in prompt


def test_gate_holds_prices_and_bot_requests(charters):
    inbox = charters["inbox_manager"]
    assert core.approval_reasons(inbox, "Hi! Happy to book a call.") == []
    assert core.approval_reasons(inbox, "Starter is $49/month.")
    assert core.approval_reasons(inbox, "We can give you a 10% discount")
    reasons = core.approval_reasons(charters["reviewer"], "NEEDS APPROVAL: custom deal\nDraft...")
    assert reasons == ["bot asked: custom deal"]


def test_message_trigger_matches_whole_words():
    r = {"trigger": {"type": "message", "keywords": ["price", "how much"]}}
    assert core.message_matches(r, {"subject": "Price?", "body": ""})
    assert core.message_matches(r, {"subject": "hi", "body": "How much is it?"})
    assert not core.message_matches(r, {"subject": "priceless tips", "body": ""})


def test_due_routines_respects_day_time_and_last_run():
    r = {"name": "brief", "trigger": {"type": "schedule", "at": "08:00", "days": ["mon"]}}
    monday_7 = datetime(2026, 11, 9, 7, 59)
    monday_8 = datetime(2026, 11, 9, 8, 0)
    tuesday_9 = datetime(2026, 11, 10, 9, 0)
    assert core.due_routines([r], monday_7, {}) == []
    assert core.due_routines([r], monday_8, {}) == [r]
    assert core.due_routines([r], monday_8, {"brief": "2026-11-09"}) == []
    assert core.due_routines([r], tuesday_9, {}) == []


def test_parse_trigger_and_save_routine(tmp_path):
    assert core.parse_trigger("keywords:pricing, quote") == {"type": "message", "keywords": ["pricing", "quote"]}
    assert core.parse_trigger("weekdays:07:30")["days"] == ["mon", "tue", "wed", "thu", "fri"]
    with pytest.raises(ValueError):
        core.parse_trigger("hourly:5")
    path = tmp_path / "routines.yaml"
    core.save_routine(path, {"name": "a", "bot": "x", "trigger": {}, "steps": "1"})
    core.save_routine(path, {"name": "a", "bot": "y", "trigger": {}, "steps": "2"})  # overwrite, not duplicate
    assert [r["bot"] for r in core.load_routines(path)] == ["y"]


def test_parsers():
    assert core.parse_picked_ids("LEAD 3: good\nLEAD 9: bad id\nLEAD 3: dup\nLEAD 1: ok", {"1", "3"}, 5) == ["3", "1"]
    assert core.review_verdict("APPROVED\nnice") == (True, "")
    assert core.review_verdict("REVISE: shorter please") == (False, "shorter please")


def test_inbox_demo_end_to_end(charters, tmp_path):
    routines = core.load_routines(ROOT / "company" / "routines.yaml")
    messages = core.load_json(ROOT / "samples" / "inbox.json")
    out = core.Output(tmp_path, "t")
    results = core.process_inbox(DemoClient(), charters, routines, messages, out)
    assert results == [("m1", "pricing_reply", "NEEDS_APPROVAL"), ("m2", None, "SKIPPED"),
                       ("m3", "pricing_reply", "NEEDS_APPROVAL")]


def test_objective_demo_revises_then_approves(charters, tmp_path):
    leads = core.load_leads(ROOT / "samples" / "leads.csv")
    out = core.Output(tmp_path, "t")
    results = core.run_objective(DemoClient(), charters, "Book intro calls with clinics", leads, out)
    assert results == [("1", "READY"), ("3", "READY"), ("5", "READY")]
    chat = (out.dir / "group_chat.md").read_text()
    assert "REVISE:" in chat and "APPROVED" in chat
    assert len(list(out.dir.glob("ready__outreach_*.md"))) == 3
