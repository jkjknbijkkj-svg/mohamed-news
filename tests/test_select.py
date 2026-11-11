from datetime import timedelta

from newsbot.models import Item
from newsbot.selection import pick_candidates
from newsbot.state import State, now_utc

CFG = {
    "limits": {"max_age_hours": 36},
    "scoring": {
        "official_bonus": 3,
        "tier_weights": {"1": 3, "2": 2, "3": 1},
        "extra_source_bonus": 2,
        "impact_keyword_bonus": 1,
        "impact_keyword_cap": 4,
        "hn_signal_bonus": 2,
        "recency_half_life_hours": 12,
    },
    "impact_keywords": {"ai": ["launches"]},
    "low_value_patterns": ["best "],
}
BANNED = {"source_blocklist": ["election"]}


def make(domain, title, url, official=False, hours=2):
    return Item(
        source_id=domain,
        source_name=domain,
        domain=domain,
        tier=2,
        official=official,
        categories=["ai"],
        title=title,
        url=url,
        published=now_utc() - timedelta(hours=hours),
        summary="",
    )


def run(items, tmp_path, state=None):
    state = state or State.load(tmp_path / "s.json")
    return pick_candidates(items, "ai", state, BANNED, CFG, set(), now_utc())


TITLE = "OpenAI launches a new agent platform for enterprise customers"


def test_official_single_source_is_eligible(tmp_path):
    assert len(run([make("openai.com", TITLE, "https://openai.com/a", official=True)], tmp_path)) == 1


def test_unofficial_single_source_is_not_eligible(tmp_path):
    assert run([make("techcrunch.com", TITLE, "https://techcrunch.com/a")], tmp_path) == []


def test_two_domains_confirm_each_other(tmp_path):
    items = [
        make("techcrunch.com", TITLE, "https://techcrunch.com/a"),
        make("theverge.com", "OpenAI launches new agent platform for enterprise customers today", "https://theverge.com/b"),
    ]
    result = run(items, tmp_path)
    assert len(result) == 1 and result[0].distinct_domains == 2


def test_low_value_title_is_dropped(tmp_path):
    item = make("openai.com", "Best AI tools you should try this week for work", "https://openai.com/c", official=True)
    assert run([item], tmp_path) == []


def test_seen_url_is_dropped(tmp_path):
    state = State.load(tmp_path / "s.json")
    item = make("openai.com", TITLE, "https://openai.com/a", official=True)
    state.mark_seen(item.url)
    assert run([item], tmp_path, state) == []
