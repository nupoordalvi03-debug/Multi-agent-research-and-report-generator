"""SQLite persistence. All workflow state lives here so sessions survive refresh and restart."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    hypothesis TEXT NOT NULL DEFAULT '',
    source_mode TEXT NOT NULL,
    report_type TEXT NOT NULL,
    citation_style TEXT NOT NULL,
    depth TEXT NOT NULL,
    mode TEXT NOT NULL,                 -- live | demo
    status TEXT NOT NULL,               -- workflow state (see workflow/state.py)
    failed_stage TEXT,
    error TEXT,
    quality_score INTEGER,
    revision_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS stage_outputs (
    session_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    output_json TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    PRIMARY KEY (session_id, stage)
);
CREATE TABLE IF NOT EXISTS agent_runs (
    session_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    status TEXT NOT NULL,               -- waiting | running | completed | failed
    progress INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    started_at TEXT,
    completed_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    completion_state TEXT,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (session_id, agent)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    level TEXT NOT NULL,                -- info | success | warning | error
    message TEXT NOT NULL,
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, id);
CREATE TABLE IF NOT EXISTS sources (
    session_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (session_id, source_id)
);
CREATE TABLE IF NOT EXISTS evidence (
    session_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (session_id, evidence_id)
);
CREATE TABLE IF NOT EXISTS claims (
    session_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (session_id, claim_id)
);
CREATE TABLE IF NOT EXISTS reports (
    session_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, version)
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    purpose TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    http_status INTEGER,
    category TEXT NOT NULL,      -- ok | rate_limit | quota_exhausted | billing | auth | ...
    latency_ms INTEGER,
    wait_s REAL,
    provider TEXT,
    model TEXT,
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_session ON llm_calls(session_id, id);
CREATE TABLE IF NOT EXISTS critiques (
    session_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, version)
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            if path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._migrate()

    # Columns added after v1; ALTER only when missing so old databases keep working.
    NEW_SESSION_COLUMNS = {
        "client_request_id": "TEXT", "llm_provider": "TEXT", "llm_model": "TEXT",
        "error_info": "TEXT", "runner_id": "TEXT", "lease_until": "REAL",
    }

    def _migrate(self) -> None:
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(sessions)").fetchall()}
        for name, typ in self.NEW_SESSION_COLUMNS.items():
            if name not in cols:
                self._conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {typ}")
        self._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_client_req "
                           "ON sessions(client_request_id) WHERE client_request_id IS NOT NULL")

    # -- low level -------------------------------------------------------
    def execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # -- sessions --------------------------------------------------------
    def create_session(self, sid: str, req: dict, mode: str, status: str, *, client_request_id: str | None = None,
                       llm_provider: str | None = None, llm_model: str | None = None) -> str:
        """Insert a session. If client_request_id was already used, return the EXISTING session id
        (idempotent submit: a double-click or resent request never creates a second workflow)."""
        ts = now_iso()
        with self._lock:
            if client_request_id:
                row = self._conn.execute("SELECT id FROM sessions WHERE client_request_id=?",
                                         (client_request_id,)).fetchone()
                if row:
                    return row["id"]
            self._conn.execute(
                "INSERT INTO sessions (id, query, hypothesis, source_mode, report_type, citation_style, depth,"
                " mode, status, created_at, updated_at, client_request_id, llm_provider, llm_model)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, req["query"], req["hypothesis"], req["source_mode"], req["report_type"],
                 req["citation_style"], req["depth"], mode, status, ts, ts, client_request_id,
                 llm_provider, llm_model))
        return sid

    # -- execution lease: one runner per session across ALL backend processes --------------
    def try_acquire_lease(self, sid: str, runner: str, ttl: float) -> bool:
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE sessions SET runner_id=?, lease_until=? WHERE id=? AND (runner_id IS NULL OR runner_id=?"
                " OR lease_until IS NULL OR lease_until < ?)", (runner, now + ttl, sid, runner, now))
            return cur.rowcount == 1

    def renew_lease(self, sid: str, runner: str, ttl: float) -> bool:
        with self._lock:
            cur = self._conn.execute("UPDATE sessions SET lease_until=? WHERE id=? AND runner_id=?",
                                     (time.time() + ttl, sid, runner))
            return cur.rowcount == 1

    def release_lease(self, sid: str, runner: str) -> None:
        self.execute("UPDATE sessions SET runner_id=NULL, lease_until=NULL WHERE id=? AND runner_id=?", (sid, runner))

    def lease_holder(self, sid: str) -> tuple[str | None, float | None]:
        r = self.query_one("SELECT runner_id, lease_until FROM sessions WHERE id=?", (sid,))
        return (r["runner_id"], r["lease_until"]) if r else (None, None)

    # -- LLM request audit log ----------------------------------------------------------
    def add_llm_call(self, sid: str, c: dict) -> None:
        self.execute("INSERT INTO llm_calls (session_id, agent, purpose, attempt, http_status, category, latency_ms,"
                     " wait_s, provider, model, ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (sid, c.get("agent", "-"), c.get("purpose", "-"), c.get("attempt", 1), c.get("status"),
                      c.get("category", "?"), c.get("latency_ms"), c.get("wait_s"), c.get("provider"),
                      c.get("model"), now_iso()))

    def get_llm_calls(self, sid: str) -> list[dict]:
        return self.query("SELECT * FROM llm_calls WHERE session_id=? ORDER BY id", (sid,))

    def update_session(self, sid: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.execute(f"UPDATE sessions SET {cols} WHERE id = ?", (*fields.values(), sid))

    def get_session(self, sid: str) -> dict | None:
        return self.query_one("SELECT * FROM sessions WHERE id = ?", (sid,))

    def list_sessions(self, limit: int = 100) -> list[dict]:
        return self.query("SELECT * FROM sessions ORDER BY created_at DESC LIMIT ?", (limit,))

    # -- stage outputs ---------------------------------------------------
    def save_stage_output(self, sid: str, stage: str, output: Any) -> None:
        self.execute("INSERT OR REPLACE INTO stage_outputs VALUES (?,?,?,?)",
                     (sid, stage, json.dumps(output), now_iso()))

    def get_stage_output(self, sid: str, stage: str) -> Any | None:
        row = self.query_one("SELECT output_json FROM stage_outputs WHERE session_id=? AND stage=?",
                             (sid, stage))
        return json.loads(row["output_json"]) if row else None

    def delete_stage_outputs_from(self, sid: str, stages: list[str]) -> None:
        for st in stages:
            self.execute("DELETE FROM stage_outputs WHERE session_id=? AND stage=?", (sid, st))

    # -- agent runs ------------------------------------------------------
    def init_agent_runs(self, sid: str, agents: list[str]) -> None:
        for a in agents:
            self.execute("INSERT OR IGNORE INTO agent_runs (session_id, agent, status) VALUES (?,?,?)",
                         (sid, a, "waiting"))

    def update_agent_run(self, sid: str, agent: str, **fields: Any) -> None:
        if "metrics" in fields:
            fields["metrics_json"] = json.dumps(fields.pop("metrics"))
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.execute(f"UPDATE agent_runs SET {cols} WHERE session_id=? AND agent=?",
                     (*fields.values(), sid, agent))

    def get_agent_runs(self, sid: str) -> list[dict]:
        rows = self.query("SELECT * FROM agent_runs WHERE session_id=?", (sid,))
        for r in rows:
            r["metrics"] = json.loads(r.pop("metrics_json") or "{}")
        return rows

    # -- events ----------------------------------------------------------
    def add_event(self, sid: str, agent: str, level: str, message: str) -> None:
        self.execute("INSERT INTO events (session_id, agent, level, message, ts) VALUES (?,?,?,?,?)",
                     (sid, agent, level, message[:500], now_iso()))

    def get_events(self, sid: str, after_id: int = 0, limit: int = 500) -> list[dict]:
        return self.query("SELECT * FROM events WHERE session_id=? AND id>? ORDER BY id LIMIT ?",
                          (sid, after_id, limit))

    # -- json-blob tables -----------------------------------------------
    def replace_items(self, table: str, key: str, sid: str, items: list[dict]) -> None:
        assert table in {"sources", "evidence", "claims"}
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute(f"DELETE FROM {table} WHERE session_id=?", (sid,))
                self._conn.executemany(
                    f"INSERT INTO {table} VALUES (?,?,?)",
                    [(sid, it[key], json.dumps(it)) for it in items])
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def get_items(self, table: str, sid: str) -> list[dict]:
        assert table in {"sources", "evidence", "claims"}
        key = {"sources": "source_id", "evidence": "evidence_id", "claims": "claim_id"}[table]
        rows = self.query(f"SELECT data_json FROM {table} WHERE session_id=? ORDER BY {key}", (sid,))
        return [json.loads(r["data_json"]) for r in rows]

    # -- reports & critiques --------------------------------------------
    def save_report(self, sid: str, version: int, data: dict) -> None:
        self.execute("INSERT OR REPLACE INTO reports VALUES (?,?,?,?)",
                     (sid, version, json.dumps(data), now_iso()))

    def get_reports(self, sid: str) -> list[dict]:
        rows = self.query("SELECT data_json FROM reports WHERE session_id=? ORDER BY version", (sid,))
        return [json.loads(r["data_json"]) for r in rows]

    def save_critique(self, sid: str, version: int, data: dict) -> None:
        self.execute("INSERT OR REPLACE INTO critiques VALUES (?,?,?,?)",
                     (sid, version, json.dumps(data), now_iso()))

    def get_critiques(self, sid: str) -> list[dict]:
        rows = self.query("SELECT data_json FROM critiques WHERE session_id=? ORDER BY version", (sid,))
        return [json.loads(r["data_json"]) for r in rows]

    def clear_downstream(self, sid: str, from_stage: str) -> None:
        """When retrying a stage, remove artefacts produced by it and later stages."""
        order = ["planning", "research", "verification", "writing", "critique"]
        idx = order.index(from_stage)
        later = order[idx:]
        self.delete_stage_outputs_from(sid, later)
        if "research" in later:
            self.execute("DELETE FROM sources WHERE session_id=?", (sid,))
            self.execute("DELETE FROM evidence WHERE session_id=?", (sid,))
        if "verification" in later:
            self.execute("DELETE FROM claims WHERE session_id=?", (sid,))
        if "writing" in later:
            self.execute("DELETE FROM reports WHERE session_id=?", (sid,))
        if "critique" in later or "writing" in later:
            self.execute("DELETE FROM critiques WHERE session_id=?", (sid,))
