"""Pilot plumbing without the API: request shape, cost guard, parsing raw batch results, leak check."""
import json

import pandas as pd
import pytest

from classify import us_earnings as ue


def test_request_params_shape():
    p = ue.params(ue.SYSTEM, ue.SCHEMA, "Revenue was $10 million.")
    assert p["model"] == "claude-haiku-5-5"
    assert p["output_config"]["format"]["type"] == "json_schema"
    assert p["output_config"]["effort"] == "low"
    assert set(ue.SCHEMA["required"]) == set(ue.SCHEMA["properties"])     # structured outputs need all required
    assert "temperature" not in p and "thinking" not in p                   # 400s on Haiku 5.5


def test_submit_refuses_over_budget_without_yes(tmp_path):
    (tmp_path / "run.json").write_text(json.dumps({"estimate": {"gbp_est": 50.0}}))
    with pytest.raises(SystemExit, match="needs the user's OK"):
        ue.submit(tmp_path)


def _result(cid, data, ok=True, stop="end_turn"):
    if not ok:
        return {"custom_id": cid, "result": {"type": "errored", "error": {"type": "api_error"}}}
    content = [{"type": "thinking", "thinking": "", "signature": "x"}, {"type": "text", "text": json.dumps(data)}]
    return {"custom_id": cid, "result": {"type": "succeeded", "message": {
        "content": content, "stop_reason": stop, "usage": {"input_tokens": 1000, "output_tokens": 200}}}}


def test_parse_results_and_leak_check(tmp_path):
    lab = {k: None for k in ue.SCHEMA["properties"]} | {"tone": 1, "events": [{"type": "buyback", "evidence": "repurchased 1m shares"}]}
    rows = [_result("e-0000320193-20-000010", lab),
            _result("e-0000320193-20-000011", None, ok=False),
            _result("l-0000320193-20-000010", {"company_guess": "Apple Inc", "ticker_guess": None, "confidence": "high"}),
            _result("l-0000789019-20-000001", {"company_guess": "Some bank", "ticker_guess": "XYZ", "confidence": "low"})]
    (tmp_path / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    labels, leaks, u = ue.parse_results(tmp_path)
    assert list(labels.accession) == ["0000320193-20-000010"]
    assert u["succeeded"] == 3 and u["errored"] == 1 and u["input"] == 3000
    sample = pd.DataFrame({"accession": ["0000320193-20-000010", "0000789019-20-000001"],
                           "names": [json.dumps(["Apple Inc."]), json.dumps(["Microsoft Corp"])],
                           "tickers": [json.dumps(["AAPL"]), json.dumps(["MSFT"])]})
    hits = ue.leak_hits(leaks, sample)
    assert hits.set_index("accession").leaked.to_dict() == {"0000320193-20-000010": True, "0000789019-20-000001": False}
