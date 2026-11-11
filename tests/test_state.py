from datetime import timedelta

from newsbot.state import State, now_utc, to_iso


def test_roundtrip_and_idempotency(tmp_path):
    path = tmp_path / "state.json"
    state = State.load(path)
    assert not state.is_published("2026-11-09", "s0800")
    state.set_pending("2026-11-09", "s0800", {"creation_id": "1"})
    state.record_published(
        {"date": "2026-11-09", "slot": "s0800", "title": "x", "timestamp": to_iso(now_utc())}
    )
    state.save()
    again = State.load(path)
    assert again.is_published("2026-11-09", "s0800")
    assert again.get_pending("2026-11-09", "s0800") is None


def test_prune_removes_old_urls(tmp_path):
    state = State.load(tmp_path / "state.json")
    state.mark_seen("https://a.com/old", now_utc() - timedelta(days=40))
    state.mark_seen("https://a.com/new")
    state.prune()
    assert not state.is_seen("https://a.com/old")
    assert state.is_seen("https://a.com/new")


def test_failures_and_llm_usage(tmp_path):
    state = State.load(tmp_path / "state.json")
    assert state.register_failure() == 1
    assert state.register_failure() == 2
    state.register_success()
    assert state.consecutive_failures == 0
    state.llm_add("2026-11-09", "model-a")
    state.llm_add("2026-11-09", "model-b", 3)
    assert state.llm_used("2026-11-09", "model-a") == 1
    assert state.llm_used("2026-11-09") == 4


def test_token_age(tmp_path):
    state = State.load(tmp_path / "state.json")
    assert state.token_age_days() is None
    state.token["issued_at"] = to_iso(now_utc() - timedelta(days=3))
    assert 2.9 < state.token_age_days() < 3.1
