"""Screen a bookmaker offer's terms before signing up: paste the full terms, get red flags.

    uv run python -W ignore -m matched.terms terms.txt          # or pipe the text on stdin

A decision model (Vercel AI Gateway, `POST /v1/decisions`) reads the terms and answers fixed questions;
the verdict follows fixed rules below. Reading documents is what these models are good at (lesson 21).
Always skim the terms yourself as well: a model can misread, and offers change.
"""
from __future__ import annotations

import sys

import httpx

GATEWAY = "https://ai-gateway.vercel.sh/v1/decisions"
MODEL = "google/gemini-decision-1"
FLAG_P = 0.5

QUESTIONS = [
    {"type": "choice", "name": "reward", "instructions": "What is the main reward a new customer receives?",
     "choices": ["sports free bets (stake not returned)", "bonus cash added to the balance",
                 "casino spins or casino bonus", "odds boost or profit boost", "cashback or refund", "other"]},
    {"type": "choice", "name": "wagering", "instructions": "How many times must the bonus or free-bet winnings be wagered (played through) before they can be withdrawn?",
     "choices": ["no wagering: winnings are withdrawable cash", "1x", "2x to 5x", "6x to 10x", "more than 10x", "not stated"]},
    {"type": "choice", "name": "min_odds", "instructions": "What are the minimum odds for the qualifying bet?",
     "choices": ["below 1.5 (1/2) or none", "1.5 to 1.99", "evens (2.0)", "above 2.0", "not stated"]},
    {"type": "predicate", "name": "balance_lock", "instructions": "Must the customer keep a minimum balance, deposit far more than the qualifying stake, or reach a withdrawal minimum above £20 before cashing out?"},
    {"type": "predicate", "name": "restricted_markets", "instructions": "Can the free bets only be used on restricted bets such as accumulators, bet builders, a single sport like racing, or specific events?"},
    {"type": "predicate", "name": "short_expiry", "instructions": "Do the free bets expire within 7 days or less of being credited?"},
    {"type": "predicate", "name": "ewallet_excluded", "instructions": "Are deposits by PayPal, Skrill, Neteller or other e-wallets excluded from the offer?"},
    {"type": "predicate", "name": "casino_required", "instructions": "Must the customer play casino, slots or games to qualify for or use the reward?"},
    {"type": "predicate", "name": "split", "instructions": "Is the free-bet reward split into several smaller free bets?"},
]


def _wire(q: dict) -> dict:
    """The gateway wants choices as objects: [{"value": ...}]."""
    return {**q, "choices": [{"value": v} for v in q["choices"]]} if q["type"] == "choice" else q


def ask(terms: str, model: str = MODEL, client: httpx.Client | None = None) -> dict:
    c = client or httpx.Client(headers={"Authorization": "Bearer proxy-injected"}, timeout=120)
    r = c.post(GATEWAY, json={"model": model, "input": "Bookmaker offer terms and conditions:\n\n" + terms[:60000],
                              "questions": [_wire(q) for q in QUESTIONS]})
    d = r.json()
    if "answers" not in d:
        raise RuntimeError(str(d.get("error", d))[:300])
    out = {}
    for a in d["answers"]:
        if a["type"] == "choice" and "choice" not in a:                 # tolerate other field names
            a["choice"] = a.get("value") or a.get("answer")
        if a["type"] == "predicate":
            out[a["name"]] = a["probability"]
        else:                                   # choice: the answer and how sure the model is
            probs = a.get("probabilities") or {}
            out[a["name"]] = (a.get("choice"), probs.get(a.get("choice")) if isinstance(probs, dict) else a.get("confidence"))
    return out


def verdict(a: dict) -> tuple[str, list[str]]:
    """Fixed rules: AVOID for anything that makes the offer hard to extract as cash; CAUTION for nuisances."""
    avoid, caution = [], []
    reward, wager, odds = a["reward"][0] or "", a["wagering"][0] or "", a["min_odds"][0] or ""
    if not reward.startswith("sports free bets"):
        (avoid if reward.startswith(("casino", "bonus cash")) else caution).append(f"reward is {reward}")
    if wager in ("2x to 5x", "6x to 10x", "more than 10x"):
        avoid.append(f"wagering {wager}")
    elif wager == "not stated":
        caution.append("wagering not stated: check by hand")
    if a["balance_lock"] >= FLAG_P:
        avoid.append("minimum balance, large deposit or high withdrawal minimum")
    if a["casino_required"] >= FLAG_P:
        avoid.append("casino play required")
    if odds == "above 2.0":
        caution.append("qualifying odds above evens: higher qualifying loss")
    for k, why in (("restricted_markets", "free bets limited to accas, bet builders or one sport (harder to hedge)"),
                   ("short_expiry", "free bets expire within 7 days")):
        if a[k] >= FLAG_P:
            caution.append(why)
    notes = [why for k, why in (("ewallet_excluded", "note: deposit by debit card, not e-wallet"),
                                ("split", "note: free bets come as several smaller ones (same value, more bets to place)"))
             if a[k] >= FLAG_P]
    v = "AVOID" if avoid else "CAUTION" if caution else "GOOD"
    return v, avoid + caution + notes


def main() -> None:
    text = open(sys.argv[1]).read() if len(sys.argv) > 1 else sys.stdin.read()
    a = ask(text)
    v, why = verdict(a)
    print(f"Verdict: {v}")
    for w in why:
        print(f"  - {w}")
    print("\nAnswers:", {k: (v if not isinstance(v, float) else round(v, 2)) for k, v in a.items()})
    print("Read the terms yourself too: models can misread, and bookmakers change terms.")


if __name__ == "__main__":
    main()
