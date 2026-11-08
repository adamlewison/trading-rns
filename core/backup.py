"""Off-machine backup of each market's data to object storage (Backblaze B2), so nothing scraped
exists only on one machine. Same layout for every market; what each one backs up is declared in
ingest/<market>/backup.py.

    uv run python -m core.backup push   --profile uk|us [--prune-raw]   # incremental upload
    uv run python -m core.backup pull   --profile uk|us [--skip-raw]    # restore into an empty data/
    uv run python -m core.backup status --profile uk|us [--remote]
    uv run python -m core.backup push-bulk | pull-bulk --profile us     # large bulk files, chunked

What is backed up (under the market's backup_prefix/):
  db/<table>.parquet               full snapshot of each small table, uploaded only if changed
  db/<incr_table>/part-*.parquet   incremental: rows fetched since the last push
  raw/part-*.tar                   incremental: cached raw downloads since the last push (never overwritten)
  bulk/<file>@<date>-<size>/chunk-*  bulk downloads in 200MB chunks, one folder per version
  status/<name>.json               progress report from the machine running the scrape
  manifest.json                    watermarks + hashes

Storage is private and only for personal research: the scraped text is never published.
Credentials: on your own machine, B2_KEY_ID / B2_APP_KEY in .env (gitignored). In the cloud
session, the key is an environment network secret injected by the proxy, never in the container.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib
import io
import json
import os
import tarfile
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import quote

import httpx

from core.config import MARKETS, load_config
from core.db import session
from core.log import get_logger

log = get_logger("backup")

RAW_PART_MAX_BYTES = 400 * 1024 * 1024


@dataclass
class Profile:
    """What one market backs up. Each market declares its own in ingest/<market>/backup.py."""
    name: str
    prefix: str                         # folder in the bucket
    db: Path
    schema: str
    raw_dir: Path                       # cached raw downloads (tar parts are relative to this)
    snapshot_tables: list[str]          # small tables: full snapshot when changed
    incr_table: str                     # large append-only table, backed up incrementally by fetched_at
    incr_key: str                       # its primary key (dedup on restore)
    manifest: Path
    status_name: str
    status_fn: Callable[[], dict] | None = None
    can_prune_raw: bool = False         # may local raw files be deleted once safely in the store?
    bulk_dir: Path | None = None        # large single files backed up in chunks (push-bulk)


def get_profile(name: str = "uk") -> Profile:
    if name not in MARKETS:
        raise ValueError(f"unknown profile {name!r}")
    return importlib.import_module(f"ingest.{name}.backup").profile()


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
    if not pf.db.exists():   # never create an empty DB and back that up over the real one
        raise SystemExit(f"[{pf.name}] no database at {pf.db}: nothing to back up "
                         "(old data layout? run deploy/migrate-layout.sh; fresh machine? `pull` first)")
    prefix = pf.prefix
    store = store or get_store()
    m = _load_manifest(store, prefix, pf.manifest)
    rows = m.setdefault("table_rows", {})
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")  # unique per push; parts are never overwritten
    uploaded = []

    with session(pf.db, pf.schema) as con:
        exports = {}
        for t in pf.snapshot_tables:
            h = _content_hash(con, t)
            if m["tables"].get(t) != h:
                n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                if n == 0 and rows.get(t, 0) > 0:   # an emptied table never overwrites a good snapshot
                    log.error("[%s] %s is empty locally but has %d rows in the backup: NOT overwriting",
                              pf.name, t, rows[t])
                    continue
                exports[t] = (h, n, _export(con, f"SELECT * FROM {t}"))
        wm = m.get("bodies_watermark")
        where = f"WHERE fetched_at > TIMESTAMP '{wm}'" if wm else ""
        new_wm = con.execute(f"SELECT max(fetched_at) FROM {pf.incr_table} {where}").fetchone()[0]
        bodies = _export(con, f"SELECT * FROM {pf.incr_table} {where}") if new_wm else None
    # uploads happen outside the DB lock so the scraper isn't blocked
    for t, (h, n, data) in exports.items():
        store.put(f"{prefix}/db/{t}.parquet", data)
        m["tables"][t] = h
        rows[t] = n
        uploaded.append((t, len(data)))
    if bodies is not None:
        store.put(f"{prefix}/db/{pf.incr_table}/part-{stamp}.parquet", bodies)
        m["bodies_watermark"] = str(new_wm)
        uploaded.append((pf.incr_table, len(bodies)))

    raw_dir: Path = pf.raw_dir
    since = m.get("raw_watermark", 0.0)
    files = sorted((p for p in raw_dir.rglob("*.gz")
                    if p.stat().st_mtime > since),
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


BULK_CHUNK = 200 * 1024 * 1024


def push_bulk(store: Store | None = None, profile: str = "us") -> None:
    """Upload the bulk downloads (SEC submissions.zip / companyfacts.zip) in 200MB chunks, so
    nothing scraped exists only on one machine. Each version is kept under its own date; already
    uploaded chunks are skipped (resumable). Restore with pull_bulk."""
    pf = get_profile(profile)
    if pf.bulk_dir is None:
        raise SystemExit(f"profile {pf.name} has no bulk files")
    bulk_dir = pf.bulk_dir
    store = store or get_store()
    m = _load_manifest(store, pf.prefix, pf.manifest)
    done = m.setdefault("bulk", {})
    for f in sorted(bulk_dir.glob("*")) if bulk_dir.exists() else []:
        st = f.stat()
        ver = f"{f.name}@{datetime.fromtimestamp(st.st_mtime):%Y%m%d}-{st.st_size}"
        rec = done.setdefault(ver, {"chunks": 0, "complete": False})
        if rec["complete"]:
            continue
        n_chunks = -(-st.st_size // BULK_CHUNK)
        sha = hashlib.sha256()
        with f.open("rb") as fh:
            for i in range(n_chunks):
                data = fh.read(BULK_CHUNK)
                sha.update(data)
                if i < rec["chunks"]:
                    continue
                store.put(f"{pf.prefix}/bulk/{ver}/chunk-{i:04d}", data)
                rec["chunks"] = i + 1
                _save_manifest(store, pf.prefix, m, pf.manifest)
                log.info("[%s] bulk %s chunk %d/%d", pf.name, f.name, i + 1, n_chunks)
        rec.update(complete=True, name=f.name, size=st.st_size, sha256=sha.hexdigest(), n_chunks=n_chunks)
        _save_manifest(store, pf.prefix, m, pf.manifest)
        log.info("[%s] bulk %s backed up (%.2f GB)", pf.name, f.name, st.st_size / 1e9)


def pull_bulk(store: Store | None = None, profile: str = "us") -> None:
    """Restore the latest backed-up version of each bulk file, verifying its SHA-256."""
    pf = get_profile(profile)
    store = store or get_store()
    m = _load_manifest(store, pf.prefix, pf.manifest)
    latest = {}
    for ver, rec in sorted(m.get("bulk", {}).items()):
        if rec.get("complete"):
            latest[rec["name"]] = (ver, rec)
    out_dir = pf.bulk_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, (ver, rec) in latest.items():
        tmp, sha = out_dir / (name + ".partial"), hashlib.sha256()
        with tmp.open("wb") as fh:
            for i in range(rec["n_chunks"]):
                data = store.get(f"{pf.prefix}/bulk/{ver}/chunk-{i:04d}")
                sha.update(data)
                fh.write(data)
        if sha.hexdigest() != rec["sha256"]:
            raise RuntimeError(f"checksum mismatch restoring {name}")
        tmp.rename(out_dir / name)
        log.info("[%s] restored bulk %s", pf.name, name)


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
            key = pf.incr_key
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
    ap.add_argument("cmd", choices=["push", "pull", "status", "push-bulk", "pull-bulk"])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--remote", action="store_true", help="status: show the status file the VPS uploads")
    ap.add_argument("--profile", choices=["uk", "us"], default="uk")
    ap.add_argument("--prune-raw", action="store_true", help="push (us only): delete local raw files once backed up")
    ap.add_argument("--skip-raw", action="store_true", help="pull: restore tables only, not the raw-file cache")
    a = ap.parse_args()
    if a.cmd == "push":
        push(profile=a.profile, prune_raw=a.prune_raw)
    elif a.cmd == "push-bulk":
        push_bulk(profile=a.profile)
    elif a.cmd == "pull-bulk":
        pull_bulk(profile=a.profile)
    elif a.cmd == "status":
        status(remote=a.remote, profile=a.profile)
    else:
        pull(force=a.force, profile=a.profile, skip_raw=a.skip_raw)
