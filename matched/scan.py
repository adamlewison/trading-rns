"""Oddsmatcher: rank bookmaker back prices against Betfair Exchange lay prices from The Odds API.

    uv run python -W ignore -m matched.scan                      # all UK bookmakers, default sports
    uv run python -W ignore -m matched.scan --book williamhill --stake 10 --free 10 --min-odds 2.0
    uv run python -W ignore -m matched.scan --sports              # list sport keys (free call)

Needs an Odds API key: the environment's network secret for api.the-odds-api.com (header x-api-key),
or ODDS_API_KEY in .env. Each sport costs 1 credit of the free 500 a month.
⚠️ The API gives no lay liquidity: check the Betfair page has enough money at that price before betting.
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd

from matched import calc

API = "https://api.the-odds-api.com/v4"
SPORTS = ["soccer_epl", "soccer_efl_champ", "soccer_uefa_champs_league", "soccer_spain_la_liga",
          "soccer_germany_bundesliga", "soccer_italy_serie_a", "tennis_atp_aus_open_singles", "basketball_nba",
          "americanfootball_nfl", "icehockey_nhl"]
EXCHANGES = {"betfair_ex_uk": 0.05, "betfair_ex_eu": 0.05, "betfair": 0.05, "smarkets": 0.02, "matchbook": 0.02}


def _client() -> httpx.Client:
    from core.config import load_config
    load_config()                                           # .env
    key = os.environ.get("ODDS_API_KEY") or "proxy-injected"
    return httpx.Client(base_url=API, headers={"x-api-key": key}, timeout=30)


def sports(cl: httpx.Client) -> pd.DataFrame:
    r = cl.get("/sports")
    r.raise_for_status()
    return pd.DataFrame(r.json())[["key", "group", "title", "active"]]


def fetch(cl: httpx.Client, sport: str) -> tuple[list, str]:
    r = cl.get(f"/sports/{sport}/odds", params={"regions": "uk", "markets": "h2h", "oddsFormat": "decimal"})
    if r.status_code in (404, 422):
        return [], r.headers.get("x-requests-remaining", "?")
    r.raise_for_status()
    return r.json(), r.headers.get("x-requests-remaining", "?")


def pairs(events: list, stake: float, free: float, min_odds: float, max_odds: float,
          books: set[str] | None, hours: float) -> pd.DataFrame:
    """One row per (event, outcome, bookmaker): the best exchange lay for that outcome against the back price."""
    now = datetime.now(timezone.utc)
    rows = []
    for ev in events:
        start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        if not now < start < now + timedelta(hours=hours):
            continue
        lays: dict[str, tuple[float, str, float]] = {}
        backs: list[tuple[str, str, float]] = []
        for bk in ev["bookmakers"]:
            for mk in bk["markets"]:
                for o in mk["outcomes"]:
                    if mk["key"] == "h2h_lay" and bk["key"] in EXCHANGES:
                        c = EXCHANGES[bk["key"]]
                        if o["name"] not in lays or o["price"] < lays[o["name"]][0]:
                            lays[o["name"]] = (o["price"], bk["title"], c)
                    elif mk["key"] == "h2h" and bk["key"] not in EXCHANGES:
                        if books is None or bk["key"] in books:
                            backs.append((bk["title"], o["name"], o["price"]))
        for book, name, back in backs:
            if name not in lays or not min_odds <= back <= max_odds:
                continue
            lay, ex, c = lays[name]
            q = calc.qualifying(stake, back, lay, c)
            f = calc.free_bet_snr(free, back, lay, c)
            rows.append({"start": start.strftime("%a %d %b %H:%M"), "event": f"{ev['home_team']} v {ev['away_team']}",
                         "sport": ev["sport_key"], "bet_on": name, "bookmaker": book, "back": back, "exchange": ex,
                         "lay": lay, "rating": calc.rating(back, lay), "qual_result": q.result, "qual_lay": q.lay_stake,
                         "qual_liability": q.liability, "free_kept": f.result, "free_lay": f.lay_stake,
                         "free_liability": f.liability})
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sports", action="store_true", help="list sport keys and exit (free)")
    ap.add_argument("--sport", action="append", help="sport key; repeat for several (default: a football-heavy list)")
    ap.add_argument("--book", action="append", help="bookmaker key, e.g. williamhill, paddypower, skybet")
    ap.add_argument("--stake", type=float, default=10.0)
    ap.add_argument("--free", type=float, default=10.0)
    ap.add_argument("--min-odds", type=float, default=1.5)
    ap.add_argument("--max-odds", type=float, default=12.0)
    ap.add_argument("--hours", type=float, default=72.0, help="only events starting within this many hours")
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args()
    cl = _client()
    if a.sports:
        print(sports(cl).to_string(index=False))
        return
    events, left = [], "?"
    for s in a.sport or SPORTS:
        ev, left = fetch(cl, s)
        events += ev
    df = pairs(events, a.stake, a.free, a.min_odds, a.max_odds, set(a.book) if a.book else None, a.hours)
    print(f"{len(events)} events, {len(df)} back/lay pairs; API credits left this month: {left}")
    if df.empty:
        return
    pd.set_option("display.width", 220, "display.max_columns", 30)
    show = ["start", "event", "bet_on", "bookmaker", "back", "exchange", "lay", "rating"]
    print(f"\n== Qualifying bets (£{a.stake:g} stake): smallest loss first ==")
    q = df.sort_values("qual_result", ascending=False).head(a.top)
    print(q[show + ["qual_lay", "qual_liability", "qual_result"]].round(2).to_string(index=False))
    print(f"\n== Free bets (£{a.free:g}, stake not returned): most kept first ==")
    f = df.sort_values("free_kept", ascending=False).head(a.top)
    print(f[show + ["free_lay", "free_liability", "free_kept"]].round(2).to_string(index=False))
    print("\nBefore betting: refresh both prices, check the Betfair page has enough money at that lay price, "
          "and lay within a minute of backing.")


if __name__ == "__main__":
    main()
