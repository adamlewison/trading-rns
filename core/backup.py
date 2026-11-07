"""Off-machine backup of data/ to object storage, so a reclaimed container loses nothing.

    uv run python -m core.backup push      # incremental upload
    uv run python -m core.backup pull      # restore into an empty data/ (fresh container)
    uv run python -m core.backup status

What is backed up (under <prefix>/):
  db/<table>.parquet            full snapshot of each small table, uploaded only if changed
  db/ann_bodies/part-*.parquet  incremental: rows fetched since the last push
  raw/part-*.tar                incremental: cached raw downloads (data/raw) written since last push
  manifest.json                 watermarks + hashes

Storage is private and only for personal research: the scraped text is never published.
Credentials: Backblaze B2 key, stored as an environment network secret and injected by the
proxy into requests to api.backblazeb2.com. The key itself never sits in this repo or container.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import httpx

from core.config import load_config
from core.db import SCHEMA, session
from core.log import get_logger

log = get_logger("backup")

SNAPSHOT_TABLES = ["lse_instruments", "lse_issuer_snapshots", "universe", "prices_daily",
                   "price_download_log", "ann_index", "scrape_progress"]
RAW_PART_MAX_BYTES = 400 * 1024 * 1024


class Store(Protocol):
    def put(self, name: str, data: bytes) -> None: ...
    def get(self, name: str) -> bytes | None: ...
    def list(self, prefix: str) -> list[str]: ...


class LocalStore:
    """Directory-backed store (tests / a mounted disk)."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def put(self, name, data):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, name):
        p = self.root / name
        return p.read_bytes() if p.exists() else None

    def list(self, prefix):
        base = self.root / prefix
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file())


class B2Store:
    """Backblaze B2 native API (v3). The proxy injects the real Basic credential into the
    authorize call; we send a placeholder of the right shape."""

    AUTH_URL = "https://api.backblazeb2.com/b2api/v3/b2_authorize_account"

    def __init__(self, bucket_name: str | None = None):
        self.http = httpx.Client(timeout=300)
        placeholder = base64.b64encode(b"proxy:injected").decode()
        r = self.http.get(self.AUTH_URL, headers={"Authorization": f"Basic {placeholder}"})
        r.raise_for_status()
        a = r.json()
        api = a["apiInfo"]["storageApi"]
        self.token, self.api_url, self.download_url = a["authorizationToken"], api["apiUrl"], api["downloadUrl"]
        allowed = api.get("allowed", {}).get("buckets") or []
        if bucket_name is None and allowed:
            bucket_name = allowed[0]["name"]
        if not bucket_name:
            raise RuntimeError("no bucket: restrict the key to one bucket or set backup.bucket")
        self.bucket = bucket_name
        b = next((x for x in allowed if x.get("name") == bucket_name), None)
        if b:
            self.bucket_id = b["id"]
        else:
            r = self._api("b2_list_buckets", {"accountId": a["accountId"], "bucketName": bucket_name})
            self.bucket_id = r["buckets"][0]["bucketId"]

    def _api(self, op: str, body: dict) -> dict:
        r = self.http.post(f"{self.api_url}/b2api/v3/{op}", json=body, headers={"Authorization": self.token})
        r.raise_for_status()
        return r.json()

    def put(self, name, data):
        for attempt in range(5):
            try:
                up = self._api("b2_get_upload_url", {"bucketId": self.bucket_id})
                r = self.http.post(up["uploadUrl"], content=data, headers={
                    "Authorization": up["authorizationToken"], "X-Bz-File-Name": quote(name, safe="/"),
                    "Content-Type": "b2/x-auto", "X-Bz-Content-Sha1": hashlib.sha1(data).hexdigest(),
                })
                r.raise_for_status()
                return
            except httpx.HTTPError as e:
                log.warning("upload %s failed (%s), attempt %d", name, e, attempt + 1)
                time.sleep(5 * 2**attempt)
        raise RuntimeError(f"upload failed: {name}")

    def get(self, name):
        r = self.http.get(f"{self.download_url}/file/{self.bucket}/{quote(name, safe='/')}",
                          headers={"Authorization": self.token})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content

    def list(self, prefix):
        out, start = [], None
        while True:
            body = {"bucketId": self.bucket_id, "prefix": prefix, "maxFileCount": 1000}
            if start:
                body["startFileName"] = start
            r = self._api("b2_list_file_names", body)
            out += [f["fileName"] for f in r["files"]]
            start = r.get("nextFileName")
            if not start:
                return out


def get_store() -> Store:
    cfg = load_config().get("backup", {})
    if cfg.get("store", "b2") == "local":
        return LocalStore(Path(cfg["local_dir"]))
    return B2Store(cfg.get("bucket"))


# ------------------------------------------------------------------ push / pull
def _manifest_path() -> Path:
    return load_config()["paths"]["data_dir"] / "backup_manifest.json"


def _load_manifest(store: Store, prefix: str) -> dict:
    p = _manifest_path()
    if p.exists():
        return json.loads(p.read_text())
    remote = store.get(f"{prefix}/manifest.json")
    return json.loads(remote) if remote else {"tables": {}, "bodies_watermark": None, "raw_watermark": 0.0}


def _save_manifest(store: Store, prefix: str, m: dict) -> None:
    m["updated_at"] = datetime.now().isoformat()
    data = json.dumps(m, indent=1).encode()
    _manifest_path().write_bytes(data)
    store.put(f"{prefix}/manifest.json", data)


def _export(con, sql: str) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "x.parquet"
        con.execute(f"COPY ({sql}) TO '{f}' (FORMAT parquet, COMPRESSION zstd)")
        return f.read_bytes()


def _content_hash(con, table: str) -> str:
    """Order-independent hash of a table's content (parquet bytes are not deterministic)."""
    h = con.execute(f"SELECT count(*), coalesce(sum(hash(t)), 0) FROM {table} t").fetchone()
    return f"{h[0]}:{h[1]}"


def push(store: Store | None = None) -> dict:
    cfg = load_config()
    prefix = cfg.get("backup", {}).get("prefix", "trading-rns/v1")
    store = store or get_store()
    m = _load_manifest(store, prefix)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")  # unique per push; parts are never overwritten
    uploaded = []

    with session() as con:
        exports = {}
        for t in SNAPSHOT_TABLES:
            h = _content_hash(con, t)
            if m["tables"].get(t) != h:
                exports[t] = (h, _export(con, f"SELECT * FROM {t}"))
        wm = m.get("bodies_watermark")
        where = f"WHERE fetched_at > TIMESTAMP '{wm}'" if wm else ""
        new_wm = con.execute(f"SELECT max(fetched_at) FROM ann_bodies {where}").fetchone()[0]
        bodies = _export(con, f"SELECT * FROM ann_bodies {where}") if new_wm else None
    # uploads happen outside the DB lock so the scraper isn't blocked
    for t, (h, data) in exports.items():
        store.put(f"{prefix}/db/{t}.parquet", data)
        m["tables"][t] = h
        uploaded.append((t, len(data)))
    if bodies is not None:
        store.put(f"{prefix}/db/ann_bodies/part-{stamp}.parquet", bodies)
        m["bodies_watermark"] = str(new_wm)
        uploaded.append(("ann_bodies", len(bodies)))

    raw_dir: Path = cfg["paths"]["raw_dir"]
    since = m.get("raw_watermark", 0.0)
    files = sorted((p for p in raw_dir.rglob("*.gz") if p.stat().st_mtime > since), key=lambda p: p.stat().st_mtime)
    part, size, n = [], 0, 0
    for i, f in enumerate(files):
        part.append(f)
        size += f.stat().st_size
        if size >= RAW_PART_MAX_BYTES or i == len(files) - 1:
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tar:
                for p in part:
                    tar.add(p, arcname=str(p.relative_to(raw_dir)))
            store.put(f"{prefix}/raw/part-{stamp}-{n:03d}.tar", buf.getvalue())
            m["raw_watermark"] = part[-1].stat().st_mtime
            uploaded.append((f"raw part {n} ({len(part)} files)", buf.tell()))
            _save_manifest(store, prefix, m)   # checkpoint after each raw part
            part, size, n = [], 0, n + 1
    _save_manifest(store, prefix, m)
    for what, nbytes in uploaded:
        log.info("pushed %s: %.1f MB", what, nbytes / 1e6)
    if not uploaded:
        log.info("nothing new to push")
    return m


def pull(store: Store | None = None, force: bool = False) -> None:
    cfg = load_config()
    prefix = cfg.get("backup", {}).get("prefix", "trading-rns/v1")
    db_path: Path = cfg["paths"]["db"]
    if db_path.exists() and not force:
        raise SystemExit(f"{db_path} exists; refusing to overwrite (use --force)")
    store = store or get_store()
    if db_path.exists():
        db_path.unlink()
    names = store.list(f"{prefix}/")
    with tempfile.TemporaryDirectory() as d, session() as con:
        con.execute(SCHEMA)
        for t in SNAPSHOT_TABLES:
            data = store.get(f"{prefix}/db/{t}.parquet")
            if data is None:
                continue
            f = Path(d) / f"{t}.parquet"
            f.write_bytes(data)
            con.execute(f"INSERT INTO {t} SELECT * FROM '{f}'")
        parts = sorted(n for n in names if n.startswith(f"{prefix}/db/ann_bodies/"))
        for i, name in enumerate(parts):
            f = Path(d) / f"b{i}.parquet"
            f.write_bytes(store.get(name))
        if parts:
            con.execute(f"""INSERT INTO ann_bodies SELECT * EXCLUDE (rn) FROM (
                SELECT *, row_number() OVER (PARTITION BY ann_id ORDER BY fetched_at DESC) rn
                FROM read_parquet('{d}/b*.parquet')) WHERE rn = 1""")
    raw_dir: Path = cfg["paths"]["raw_dir"]
    for name in sorted(n for n in names if n.startswith(f"{prefix}/raw/")):
        with tarfile.open(fileobj=io.BytesIO(store.get(name))) as tar:
            tar.extractall(raw_dir, filter="data")
    remote = store.get(f"{prefix}/manifest.json")
    if remote:
        _manifest_path().write_bytes(remote)
    log.info("restored %d table snapshots, %d body parts, raw parts from %s", len(SNAPSHOT_TABLES),
             len(parts), prefix)


def status(store: Store | None = None) -> None:
    prefix = load_config().get("backup", {}).get("prefix", "trading-rns/v1")
    store = store or get_store()
    names = store.list(f"{prefix}/")
    print(f"{len(names)} objects under {prefix}/")
    m = store.get(f"{prefix}/manifest.json")
    print(json.loads(m) if m else "no manifest")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["push", "pull", "status"])
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    {"push": push, "status": status}.get(a.cmd, lambda: pull(force=a.force))()
