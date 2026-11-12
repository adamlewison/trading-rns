import json

from classify.labels import parse, to_frame


def _ok(cid, data):
    return {"custom_id": cid, "result": {"type": "succeeded", "message": {
        "content": [{"type": "text", "text": json.dumps(data)}], "stop_reason": "end_turn"}}}


def test_success_wins_over_expired_and_first_success_is_kept():
    res = [{"custom_id": "e-A", "result": {"type": "errored", "error": {"code": "batch_expired"}}},
           _ok("e-A", {"tone": 1, "events": [{"type": "buyback", "evidence": "x"}]}),
           _ok("e-A", {"tone": -2, "events": []}),
           {"custom_id": "e-B", "result": {"type": "succeeded", "message": {
               "content": [{"type": "text", "text": "{not json"}], "stop_reason": "end_turn"}}}]
    labels, n = parse(res)
    assert labels["A"]["tone"] == 1 and "B" not in labels
    assert n["errored"] == 1 and n["bad_json"] == 1
    df = to_frame(labels)
    assert df.loc[0, "ev_buyback"] == 1 and df.loc[0, "ev_ceo_change"] == 0 and df.loc[0, "n_events"] == 1
