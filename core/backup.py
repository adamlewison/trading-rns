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
Credentials: on your own machine, B2_KEY_ID / B2_APP_KEY in .env (gitignored). In the cloud
session, the key is an environment network secret injected by the proxy, never in the container.
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
from dataclasses import dataclass
from typing import Callable, Protocol
from urllib.parse import quote

import httpx

from core.config import load_config
from core.db import SCHEMA, session
from core.log import get_logger

log = get_logger("backup")

SNAPSHOT_TABLES = ["lse_instruments", "lse_issuer_snapshots", "universe", "prices_daily",
                   "price_download_log", "ann_index", "scrape_progress"]
RAW_PART_MAX_BYTES = 400 * 1024 * 1024


@dataclass
class Profile:
    """What to back up. 'uk' is the original layout (unchanged); 'us' is the SEC EDGAR data."""
    name: str
    prefix: str
    db: Path
    schema: str
    raw_dir: Path
    snapshot_tables: list[str]
    incr_table: str                 # append-only table backed up incrementally by fetched_at
    manifest: Path
    status_name: str
    status_fn: Callable[[], dict] | None = None
    raw_exclude: tuple[str, ...] = ()   # top-level subfolders of raw_dir that belong to another profile
    can_prune_raw: bool = False         # may local raw files be deleted once safely in the store?


def get_profile(name: str = "uk") -> Profile:
    cfg = load_config()
    prefix = cfg.get("backup", {}).get("prefix", "trading-rns/v1")
    if name == "uk":
        return Profile("uk", prefix, cfg["paths"]["db"], SCHEMA, cfg["paths"]["raw_dir"], SNAPSHOT_TABLES,
                       "ann_bodies", cfg["paths"]["data_dir"] / "backup_manifest.json", "status.json",
                       lambda: _status(cfg), raw_exclude=("us",))
    if name == "us":
        from ingest.us.common import US_SCHEMA, us_cfg
        u = us_cfg()
        return Profile("us", prefix.replace("trading-rns/", "trading-rns/us/"), u["db"], US_SCHEMA,
                       u["raw_dir"] / "sec",
                       ["sec_form_index", "sec_companies", "sec_filings", "sec_ticker_map", "us_facts",
                        "us_prices_daily", "us_price_download_log", "us_progress"],
                       "us_filing_docs", u["db"].parent / "backup_manifest_us.json", "us_status.json",
                       _us_status, can_prune_raw=True)
    raise ValueError(name)


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
        # On your own machine: B2_KEY_ID / B2_APP_KEY from .env. In the cloud session the proxy
        # injects the real credential, so a placeholder of the right shape is sent instead.
        kid, key = os.environ.get("B2_KEY_ID"), os.environ.get("B2_APP_KEY")
        cred = f"{kid}:{key}" if kid and key else "proxy:injected"
        r = self.http.get(self.AUTH_URL, headers={"Authorization": "Basic " + base64.b64encode(cred.encode()).decode()})
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
def _load_manifest(store: Store, prefix: str, p: Path) -> dict:
    if p.exists():
        return json.loads(p.read_text())
    remote = store.get(f"{prefix}/manifest.json")
    return json.loads(remote) if remote else {"tables": {}, "bodies_watermark": None, "raw_watermark": 0.0}


def _save_manifest(store: Store, prefix: str, m: dict, p: Path) -> None:
    m["updated_at"] = datetime.now().isoformat()
    data = json.dumps(m, indent=1).encode()
    p.write_bytes(data)
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


def push(store: Store | None = None, profile: str = "uk", prune_raw: bool = False) -> dict:
    """prune_raw: after a raw part has been uploaded (and the manifest saved), delete those local
    files to save disk. Only allowed for profiles that opt in (US): the database rows already
    hold the extracted text, and `pull` brings the raw files back if they're ever needed."""
    pf = get_profile(profile)
    if prune_raw and not pf.can_prune_raw:
        raise SystemExit(f"--prune-raw is not allowed for profile {pf.name}")
    prefix = pf.prefix
    store = store or get_store()
    m = _load_manifest(store, prefix, pf.manifest)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")  # unique per push; parts are never overwritten
    uploaded = []

    with session(db=pf.db, schema=pf.schema) as con:
        exports = {}
        for t in pf.snapshot_tables:
            h = _content_hash(con, t)
            if m["tables"].get(t) != h:
                exports[t] = (h, _export(con, f"SELECT * FROM {t}"))
        wm = m.get("bodies_watermark")
        where = f"WHERE fetched_at > TIMESTAMP '{wm}'" if wm else ""
        new_wm = con.execute(f"SELECT max(fetched_at) FROM {pf.incr_table} {where}").fetchone()[0]
        bodies = _export(con, f"SELECT * FROM {pf.incr_table} {where}") if new_wm else None
    # uploads happen outside the DB lock so the scraper isn't blocked
    for t, (h, data) in exports.items():
        store.put(f"{prefix}/db/{t}.parquet", data)
        m["tables"][t] = h
        uploaded.append((t, len(data)))
    if bodies is not None:
        store.put(f"{prefix}/db/{pf.incr_table}/part-{stamp}.parquet", bodies)
        m["bodies_watermark"] = str(new_wm)
        uploaded.append((pf.incr_table, len(bodies)))

    raw_dir: Path = pf.raw_dir
    since = m.get("raw_watermark", 0.0)
    files = sorted((p for p in raw_dir.rglob("*.gz")
                    if p.stat().st_mtime > since and p.relative_to(raw_dir).parts[0] not in pf.raw_exclude),
                   key=lambda p: p.stat().st_mtime) if raw_dir.exists() else []
    part, size, n, pruned = [], 0, 0, 0
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
            _save_manifest(store, prefix, m, pf.manifest)   # checkpoint after each raw part
            if prune_raw:   # store.put raised if the upload failed (B2 also checks the SHA1), so it's safe
                for p in part:
                    p.unlink(missing_ok=True)
                pruned += len(part)
            part, size, n = [], 0, n + 1
    _save_manifest(store, prefix, m, pf.manifest)
    try:
        if pf.status_fn:
            store.put(f"{prefix}/status/{pf.status_name}", json.dumps(pf.status_fn(), indent=1, default=str).encode())
    except Exception as e:  # status is best-effort, never fail a backup for it
        log.warning("status upload failed: %s", e)
    for what, nbytes in uploaded:
        log.info("[%s] pushed %s: %.1f MB", pf.name, what, nbytes / 1e6)
    if pruned:
        log.info("[%s] pruned %d local raw files (now only in the backup)", pf.name, pruned)
    if not uploaded:
        log.info("[%s] nothing new to push", pf.name)
    return m


def _free_gb(path: Path) -> float:
    import shutil
    return round(shutil.disk_usage(path).free / 1e9, 2)


def _status(cfg) -> dict:
    """Small progress report so the run can be monitored remotely (read with `status --remote`)."""
    import platform
    import subprocess
    log_dir: Path = cfg["paths"]["log_dir"]

    def tail(name, n=15):
        f = log_dir / name
        return f.read_text(errors="replace").splitlines()[-n:] if f.exists() else []

    with session(read_only=True) as con:
        prog = con.execute("""SELECT split_part(key, ':', 1) AS kind, status, count(*) AS n, max(key) AS last_key
                              FROM scrape_progress GROUP BY ALL ORDER BY ALL""").df().to_dict("records")
        bodies = con.execute("SELECT status, count(*) AS n FROM ann_bodies GROUP BY 1").df().to_dict("records")
    procs = subprocess.run(["pgrep", "-fa", "run_full|backup_loop|ingest\\."], capture_output=True, text=True).stdout
    return {"host": platform.node(), "at": datetime.now().isoformat(),
            "uptime": Path("/proc/uptime").read_text().split()[0] if Path("/proc/uptime").exists() else None,
            "disk_free_gb": _free_gb(cfg["paths"]["data_dir"]),
            "progress": prog, "bodies": bodies, "processes": procs.splitlines()[:10],
            "announcements_log": tail("announcements.log"), "full_run": tail("full_run.out", 8),
            "http_errors": [l for l in tail("http.log", 200) if "WARNING" in l or "ERROR" in l][-10:]}


def _us_status() -> dict:
    import platform
    from ingest.us.common import us_cfg
    u = us_cfg()
    log_dir: Path = load_config()["paths"]["log_dir"]
    f = log_dir / "us.log"
    with session(read_only=True, db=u["db"], schema="") as con:
        prog = con.execute("SELECT key, status, n FROM us_progress ORDER BY key").df().to_dict("records")
        docs = con.execute("""SELECT item_group, status, count(*) n FROM us_filing_docs GROUP BY ALL
                              ORDER BY ALL""").df().to_dict("records")
    return {"host": platform.node(), "at": datetime.now().isoformat(),
            "uptime": Path("/proc/uptime").read_text().split()[0] if Path("/proc/uptime").exists() else None,
            "disk_free_gb": _free_gb(u["db"].parent),
            "progress": prog[-8:], "docs": docs,
            "us_log": f.read_text(errors="replace").splitlines()[-15:] if f.exists() else []}


def pull(store: Store | None = None, force: bool = False, profile: str = "uk", skip_raw: bool = False) -> None:
    pf = get_profile(profile)
    prefix = pf.prefix
    db_path: Path = pf.db
    if db_path.exists() and not force:
        raise SystemExit(f"{db_path} exists; refusing to overwrite (use --force)")
    store = store or get_store()
    if db_path.exists():
        db_path.unlink()
    names = store.list(f"{prefix}/")
    parts = []
    with tempfile.TemporaryDirectory() as d, session(db=pf.db, schema=pf.schema) as con:
        for t in pf.snapshot_tables:
            data = store.get(f"{prefix}/db/{t}.parquet")
            if data is None:
                continue
            f = Path(d) / f"{t}.parquet"
            f.write_bytes(data)
            con.execute(f"INSERT INTO {t} SELECT * FROM '{f}'")
        parts = sorted(n for n in names if n.startswith(f"{prefix}/db/{pf.incr_table}/"))
        for i, name in enumerate(parts):
            f = Path(d) / f"b{i}.parquet"
            f.write_bytes(store.get(name))
        if parts:
            key = "ann_id" if pf.incr_table == "ann_bodies" else "accession"
            con.execute(f"""INSERT INTO {pf.incr_table} SELECT * EXCLUDE (rn) FROM (
                SELECT *, row_number() OVER (PARTITION BY {key} ORDER BY fetched_at DESC) rn
                FROM read_parquet('{d}/b*.parquet')) WHERE rn = 1""")
    raw_dir: Path = pf.raw_dir
    raw_dir.mkdir(parents=True, exist_ok=True)
    for name in ([] if skip_raw else sorted(n for n in names if n.startswith(f"{prefix}/raw/"))):
        with tarfile.open(fileobj=io.BytesIO(store.get(name))) as tar:
            tar.extractall(raw_dir, filter="data")
    remote = store.get(f"{prefix}/manifest.json")
    if remote:
        pf.manifest.write_bytes(remote)
    log.info("[%s] restored %d table snapshots, %d incremental parts, raw parts from %s", pf.name,
             len(pf.snapshot_tables), len(parts), prefix)


def status(store: Store | None = None, remote: bool = False, profile: str = "uk") -> None:
    pf = get_profile(profile)
    prefix = pf.prefix
    store = store or get_store()
    if remote:
        s = store.get(f"{prefix}/status/{pf.status_name}")
        print(json.dumps(json.loads(s), indent=1) if s else "no remote status yet")
        return
    names = store.list(f"{prefix}/")
    print(f"{len(names)} objects under {prefix}/")
    m = store.get(f"{prefix}/manifest.json")
    print(json.loads(m) if m else "no manifest")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["push", "pull", "status"])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--remote", action="store_true", help="status: show the status file the VPS uploads")
    ap.add_argument("--profile", choices=["uk", "us"], default="uk")
    ap.add_argument("--prune-raw", action="store_true", help="push (us only): delete local raw files once backed up")
    ap.add_argument("--skip-raw", action="store_true", help="pull: restore tables only, not the raw-file cache")
    a = ap.parse_args()
    if a.cmd == "push":
        push(profile=a.profile, prune_raw=a.prune_raw)
    elif a.cmd == "status":
        status(remote=a.remote, profile=a.profile)
    else:
        pull(force=a.force, profile=a.profile, skip_raw=a.skip_raw)
