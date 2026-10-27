import time

from fastapi.testclient import TestClient

from persona_os.distill import text as tx
from persona_os.infra.fake_model import FakePersonaModel
from persona_os.infra.openai_model import ModelCallError
from tests.test_aftercare import AUTH

SCRIPT = """场景一
小希：我不想让大家失望，这次我一定要做好。
阿杰：你总是这样，别太勉强自己。
小希：朋友有事，我不能不管。联系我 xiaoxi@example.com
旁白：夜深了。
"""


def test_split_covers_text_and_tolerates_reflowed_quotes():
    text = ("一行字\n" * 1500) + "x" * 4000
    spans = tx.split(text, 3000)
    assert spans[0].start == 0 and spans[-1].end == len(text)
    assert all(a.end == b.start for a, b in zip(spans, spans[1:], strict=False))
    assert all(sp.end - sp.start <= 3000 for sp in spans)
    assert tx.locate("我不想让大家失望，\n这次", "小希：我不想让大家失望，这次") == 3
    assert tx.locate("我没说过", "小希：我不想") == -1
    assert tx.speakers([SCRIPT]) == [("小希", 2)]
    assert tx.mask("a@b.cn 和 12345678") == "［已遮蔽］ 和 ［已遮蔽］"


def setup(client: TestClient, text: str = SCRIPT) -> str:
    did = client.post("/v1/distillations", headers=AUTH, json={"title": "测试"}).json()[
        "distillation_id"]
    r = client.post(f"/v1/distillations/{did}/sources", headers=AUTH,
                    json={"name": "script.txt", "text": text})
    assert r.status_code == 200, r.text
    return did


def start(client: TestClient, did: str, name: str = "小希") -> dict:
    r = client.post(f"/v1/distillations/{did}:start", headers=AUTH, json={
        "target": {"name": name, "aliases": ["希希"]}, "persona_id": "xiaoxi"})
    assert r.status_code == 200, r.text
    return r.json()


def finished(client: TestClient, did: str) -> dict:
    for _ in range(300):
        body = client.get(f"/v1/distillations/{did}", headers=AUTH).json()
        if body["stage"] != "running":
            return body
        time.sleep(0.02)
    raise AssertionError(f"distillation did not finish: {body}")


def test_distill_review_and_commit_b0(make):
    model = FakePersonaModel()
    client = make(model)
    did = setup(client)
    assert client.get(f"/v1/distillations/{did}/characters", headers=AUTH).json() == [
        {"name": "小希", "lines": 2}]
    preview = client.post(f"/v1/distillations/{did}:preview", headers=AUTH,
                          json={"name": "小希"}).json()
    assert preview["relevant_segments"] == 1 and preview["mentions"] == 2
    assert preview["estimated_calls"] == 14  # 1 extraction + 12 inductions + 1 review

    start(client, did)
    body = finished(client, did)
    assert body["stage"] == "review", body
    # 2 value quotes (the invented one was dropped); no trait quotes, so each Big Five domain got
    # a focused look, which filed the same 2 lines under that domain
    assert body["progress"]["evidence"] == 2 + 5 * 2
    assert model.calls.count("extract_evidence") == 1 and model.calls.count("extract_focus") == 5
    # extract, 5 focused looks, value + one induction per Big Five domain, review
    assert body["usage"]["model_calls"] == 1 + 5 + 6 + 1

    entries = client.get(f"/v1/distillations/{did}/entries", headers=AUTH).json()
    traits = sorted(x["entry"]["construct_id"] for x in entries if x["entry"]["layer"] == "L1")
    assert traits == ["trait.agreeableness.compassion", "trait.conscientiousness.organization",
                      "trait.extraversion.sociability", "trait.neuroticism.anxiety",
                      "trait.openness.intellectual_curiosity"]  # every domain, its own facets
    entries = [x for x in entries if x["entry"]["layer"] != "L1"]
    assert len(entries) == 1  # the entry without evidence was dropped
    e = entries[0]
    assert e["status"] == "proposed"
    assert e["review_note"] == "fake: 可能过度概括。建议：加上适用情境"
    assert e["entry"]["source"] == "inference"
    assert e["entry"]["value"] == {"baseline": 0.8, "scale": "0-1"}
    assert "ev:999999" not in e["entry"]["evidence_refs"]
    assert any("［已遮蔽］" in q["quote"] for q in e["evidence"])  # e-mail masked

    ev = client.get(f"/v1/distillations/{did}/evidence/{e['evidence'][0]['evidence_id']}",
                    headers=AUTH).json()
    assert ev["source"] == "script.txt"
    assert ev["segment_text"][ev["quote_offset"]:].startswith(ev["quote"][:5])

    entry_id = e["entry"]["entry_id"]
    r = client.patch(f"/v1/distillations/{did}/entries/{entry_id}", headers=AUTH,
                     json={"text": "我把朋友看得很重。"})
    assert r.json()["status"] == "edited" and r.json()["original_text"] == "我很看重身边的人。"
    r = client.post(f"/v1/distillations/{did}/entries", headers=AUTH, json={
        "layer": "L1", "construct": "trait", "construct_id": "trait.extraversion.sociability",
        "text": "我在熟人面前话很多。"})
    assert r.json()["status"] == "added" and r.json()["entry"]["source"] == "configured"

    result = client.post(f"/v1/distillations/{did}:commit", headers=AUTH).json()
    assert result["entries"] == 2
    version = client.get(f"/v1/personas/xiaoxi/versions/{result['version_id']}",
                         headers=AUTH).json()
    texts = {x["entry_id"]: x["text"] for x in version["entries"]}
    assert texts[entry_id] == "我把朋友看得很重。"
    assert "trait.extraversion" not in result["unknown_constructs"]

    # Still editable after commit; committing again replaces B0 before any evolution.
    client.patch(f"/v1/distillations/{did}/entries/{entry_id}", headers=AUTH,
                 json={"status": "rejected"})
    again = client.post(f"/v1/distillations/{did}:commit", headers=AUTH).json()
    assert again["entries"] == 1
    assert len(client.get("/v1/personas/xiaoxi/versions", headers=AUTH).json()) == 1


def test_start_and_review_rules(make):
    client = make(FakePersonaModel())
    did = setup(client)
    r = client.post(f"/v1/distillations/{did}:start", headers=AUTH, json={
        "target": {"name": "没有这个人"}, "persona_id": "x"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_mentions"
    assert client.post(f"/v1/distillations/{did}:commit", headers=AUTH).status_code == 409
    start(client, did)
    finished(client, did)
    r = client.post(f"/v1/distillations/{did}/sources", headers=AUTH,
                    json={"name": "more.txt", "text": "小希：再来"})
    assert r.status_code == 409
    assert client.post(f"/v1/distillations/{did}:start", headers=AUTH, json={
        "target": {"name": "小希"}, "persona_id": "x"}).status_code == 409
    for e in client.get(f"/v1/distillations/{did}/entries", headers=AUTH).json():
        client.patch(f"/v1/distillations/{did}/entries/{e['entry']['entry_id']}", headers=AUTH,
                     json={"status": "rejected"})
    r = client.post(f"/v1/distillations/{did}:commit", headers=AUTH)
    assert r.status_code == 422 and r.json()["error"]["code"] == "nothing_accepted"
    assert client.get("/v1/distillations/nope", headers=AUTH).status_code == 404


class FlakyExtract(FakePersonaModel):
    """Fails extraction for segments containing "坏" until `broken` is cleared."""

    broken = True

    async def extract_evidence(self, data):
        if self.broken and "坏" in data.segment:
            raise ModelCallError("http_400", "bad request", False)
        return await super().extract_evidence(data)


def test_failed_segments_stop_the_job_and_a_restart_retries_only_them(make):
    model = FlakyExtract()
    client = make(model)
    text = "小希：第一段\n" + "填充\n" * 1500 + "小希：坏的一段\n" + "填充\n" * 10
    did = setup(client, text)
    start(client, did)
    body = finished(client, did)
    assert body["stage"] == "failed" and body["progress"]["failed"] == 1, body
    assert model.calls.count("induce_beliefs") == 0

    model.broken = False
    start(client, did)
    body = finished(client, did)
    assert body["stage"] == "review", body
    assert body["progress"]["extracted"] == body["progress"]["relevant"] == 2
    assert model.calls.count("extract_evidence") == 2  # the good segment was not redone


def test_title_follows_the_character_and_drafts_can_be_dropped(make):
    client = make(FakePersonaModel())
    empty = client.post("/v1/distillations", headers=AUTH, json={"title": "草稿"}).json()
    assert client.post(f"/v1/distillations/{empty['distillation_id']}:discard",
                       headers=AUTH).json() == {"status": "discarded"}
    assert client.get(f"/v1/distillations/{empty['distillation_id']}",
                      headers=AUTH).status_code == 404
    did = setup(client)
    assert start(client, did)["title"] == "小希"
    finished(client, did)
    r = client.post(f"/v1/distillations/{did}:discard", headers=AUTH)  # has entries: kept
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_discardable"
