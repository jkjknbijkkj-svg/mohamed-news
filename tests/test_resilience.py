"""Publishing and content fallbacks that keep the schedule running."""

from types import SimpleNamespace

import pytest

from newsbot import pipeline
from newsbot.publish import InstagramPublisher, PublishError


class FakePublisher(InstagramPublisher):
    def __init__(self, reject=(), carousel_fails=False):
        self.s = SimpleNamespace(ig_user_id="1")
        self.ai_flag = True
        self.dropped = set()
        self.reject = set(reject)
        self.carousel_fails = carousel_fails
        self.posts = []

    def _call(self, method, path, params=None, data=None, retries=3):
        if path.endswith("/media"):
            bad = self.reject & set(data)
            if bad or (self.carousel_fails and data.get("media_type") == "CAROUSEL"):
                raise PublishError("HTTP 400 code=100 sub=2207100: Invalid parameter")
            self.posts.append(dict(data))
            return {"id": f"c{len(self.posts)}"}
        if path.endswith("/media_publish"):
            return {"id": "m1"}
        return {"status_code": "FINISHED", "permalink": "p"}


class FakeState:
    def get_pending(self, *a):
        return None

    def set_pending(self, *a):
        pass

    def save(self):
        pass


def test_rejected_optional_fields_are_dropped():
    pub = FakePublisher(reject={"is_ai_generated"})
    result = pub.publish_post(["u1", "u2"], "cap", ["a1", "a2"], FakeState(), "d", "s")
    assert result["ig_media_id"] == "m1"
    assert all("is_ai_generated" not in p for p in pub.posts)
    assert pub.posts[-1]["media_type"] == "CAROUSEL"


def test_failed_carousel_falls_back_to_cover_image():
    pub = FakePublisher(carousel_fails=True)
    result = pub.publish_post(["u1", "u2"], "cap", ["a1", "a2"], FakeState(), "d", "s")
    assert result["ig_media_id"] == "m1"
    assert pub.posts[-1]["image_url"] == "u1" and "is_carousel_item" not in pub.posts[-1]


def test_unrelated_errors_still_raise():
    class Broken(FakePublisher):
        def _call(self, *a, **k):
            raise PublishError("HTTP 190 code=190: token expired")

    with pytest.raises(PublishError):
        Broken()._create({"image_url": "u"})


def test_empty_category_falls_back_to_another(monkeypatch):
    tried = []

    def fake_news(state, llm, renderer, slot, category, out_dir, now):
        tried.append(category)
        if category != "companies":
            raise pipeline.NoContent("nothing")
        return "plan-" + category

    monkeypatch.setattr(pipeline, "_news", fake_news)
    slot = SimpleNamespace(format="single", id="s1230")
    plan = pipeline.build_plan(state=None, llm=None, renderer=None, slot=slot, category="ecommerce",
                               date_iso="d", out_dir=None, now=None, now_local=None)
    assert plan == "plan-companies"
    assert tried == ["ecommerce", "ai", "companies"]
