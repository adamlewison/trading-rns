"""Turn IndexRows into ann_index records (derived fields computed here; tested)."""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from ingest.announcements.base import IndexRow
from ingest.announcements.rules import admin_rule, headline_category, is_replacement

UK = ZoneInfo("Europe/London")
UTC = ZoneInfo("UTC")
MARKET_OPEN = time(8, 0)


def uk_to_utc(dt: datetime) -> datetime:
    """Naive UK wall-clock -> naive UTC (DST-aware). Ambiguous autumn hour -> first (BST) instance."""
    return dt.replace(tzinfo=UK).astimezone(UTC).replace(tzinfo=None)


def is_pre_open(dt_uk: datetime) -> bool:
    """Published on a weekday before the 08:00 UK open. (Bank holidays are handled in Phase 3
    when mapping to the next tradable open.)"""
    return dt_uk.weekday() < 5 and dt_uk.time() < MARKET_OPEN


ANN_INDEX_COLS = ["ann_id", "source", "source_id", "tidm", "company", "headline", "url", "supplier",
                  "is_regulatory", "published_uk", "published_utc", "pre_open", "headline_category",
                  "is_admin", "admin_rule", "is_replacement", "discovered_at"]


def to_records(rows: list[IndexRow]) -> pd.DataFrame:
    now = datetime.now()
    recs = []
    for r in rows:
        rule = admin_rule(r.headline)
        recs.append({
            "ann_id": r.ann_id, "source": r.source, "source_id": r.source_id, "tidm": r.tidm,
            "company": r.company, "headline": r.headline, "url": r.url, "supplier": r.supplier,
            "is_regulatory": r.is_regulatory, "published_uk": r.published_uk,
            "published_utc": uk_to_utc(r.published_uk), "pre_open": is_pre_open(r.published_uk),
            "headline_category": headline_category(r.headline), "is_admin": rule is not None,
            "admin_rule": rule, "is_replacement": is_replacement(r.headline), "discovered_at": now,
        })
    return pd.DataFrame(recs, columns=ANN_INDEX_COLS)


def insert_index(con, rows: list[IndexRow]) -> int:
    if not rows:
        return 0
    df = to_records(rows).drop_duplicates("ann_id")
    con.register("_new", df)
    before = con.execute("SELECT count(*) FROM ann_index").fetchone()[0]
    con.execute(f"INSERT OR IGNORE INTO ann_index SELECT {', '.join(ANN_INDEX_COLS)} FROM _new")
    con.unregister("_new")
    return con.execute("SELECT count(*) FROM ann_index").fetchone()[0] - before
