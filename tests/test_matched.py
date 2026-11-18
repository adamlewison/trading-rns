import pytest

from matched import calc


def test_qualifying_bet_equalises_and_costs_little():
    h = calc.qualifying(10, back=3.0, lay=3.1, commission=0.05)
    assert h.lay_stake == pytest.approx(10 * 3.0 / 3.05)
    assert h.if_back_wins == pytest.approx(h.if_lay_wins)          # same result either way
    assert h.result == pytest.approx(-0.6557, abs=1e-3)              # a small qualifying loss
    assert h.liability == pytest.approx(h.lay_stake * 2.1)


def test_free_bet_keeps_about_three_quarters_at_close_odds():
    h = calc.free_bet_snr(10, back=6.0, lay=6.2, commission=0.05)
    assert h.if_back_wins == pytest.approx(h.if_lay_wins)
    assert 7.5 < h.result < 8.2                                      # ~78% of a £10 free bet


def test_hand_example_matches_the_textbook_numbers():
    # £10 qualifying at 2.0 back / 2.02 lay, 5% commission: lay 10.15, loss about £0.36
    h = calc.qualifying(10, 2.0, 2.02, 0.05)
    assert round(h.lay_stake, 2) == 10.15 and round(h.result, 2) == -0.36


def test_bad_odds_rejected():
    with pytest.raises(ValueError):
        calc.qualifying(10, 1.0, 2.0)
    with pytest.raises(ValueError):
        calc.free_bet_snr(10, 2.0, 2.0, commission=5)


def test_pairs_matches_each_back_price_to_the_lowest_lay_for_that_outcome():
    from datetime import datetime, timedelta, timezone

    from matched import scan
    soon = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat().replace("+00:00", "Z")
    ev = [{"commence_time": soon, "home_team": "A", "away_team": "B", "sport_key": "soccer_epl", "bookmakers": [
        {"key": "williamhill", "title": "William Hill", "markets": [{"key": "h2h", "outcomes": [
            {"name": "A", "price": 2.0}, {"name": "B", "price": 4.0}, {"name": "Draw", "price": 3.4}]}]},
        {"key": "betfair_ex_uk", "title": "Betfair", "markets": [
            {"key": "h2h", "outcomes": [{"name": "A", "price": 2.0}]},
            {"key": "h2h_lay", "outcomes": [{"name": "A", "price": 2.02}, {"name": "B", "price": 4.3}]}]},
        {"key": "smarkets", "title": "Smarkets", "markets": [
            {"key": "h2h_lay", "outcomes": [{"name": "A", "price": 2.06}]}]}]}]
    df = scan.pairs(ev, 10, 10, 1.5, 12, None, 72)
    assert set(df.bet_on) == {"A", "B"}                       # the draw has no lay price: skipped
    a = df[df.bet_on == "A"].iloc[0]
    assert a.exchange == "Betfair" and a.lay == 2.02 and round(a.qual_result, 2) == -0.36
    assert scan.pairs(ev, 10, 10, 1.5, 12, {"paddypower"}, 72).empty


def test_ledger_adds_and_sums(tmp_path, monkeypatch):
    from matched import ledger
    monkeypatch.setattr(ledger, "LEDGER", tmp_path / "l.csv")
    ledger.add("WH", "Bet 10 get 10", "qualifying", -0.36, when="2026-11-18")
    ledger.add("WH", "10 free", "free", 7.8, when="2026-11-19")
    assert "Total profit: £7.44 from 2 bets" in ledger.summary()
