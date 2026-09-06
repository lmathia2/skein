"""Restricted lifecycle for ledger-native relational memory programs."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
from datetime import UTC, datetime
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, Literal

import duckdb
from pydantic import BaseModel, ConfigDict

ProgramState = Literal["candidate", "shadow", "active", "retired"]
_MAX_RESULT_ROWS = 10_000


def _execute_bounded_sql(sender: Connection, database: str, sql: str, task_id: str,
                         max_rows: int, max_bytes: int, max_scan_events: int,
                         watermark: int | None, recorded_before: str | None) -> None:
    """Child owns all SQL work; parent may terminate it, including connection setup."""
    try:
        with duckdb.connect(config={"memory_limit": "64MB", "threads": "1", "temp_directory": ""}) as connection:
            connection.execute(f"ATTACH '{database.replace(chr(39), chr(39) * 2)}' AS source (READ_ONLY)")
            clauses = ["task_id=?"]
            params: list[Any] = [task_id]
            if watermark is not None:
                clauses.append("sequence<=?")
                params.append(watermark)
            if recorded_before is not None:
                clauses.append("recorded_at<=?")
                params.append(recorded_before)
            where = " AND ".join(clauses)
            count, size = connection.execute(
                "SELECT count(*), coalesce(sum(length(payload_json)),0) FROM source.ledger_events WHERE " + where,
                params,
            ).fetchone() or (0, 0)
            if count > max_scan_events or size > 16_000_000:
                raise OverflowError("SQL source scan budget exceeded")
            connection.execute("CREATE TABLE ledger_events AS SELECT * FROM source.ledger_events WHERE " + where, params)
            connection.execute("DETACH source")
            connection.execute("SET enable_external_access=false")
            cursor = connection.execute(f"SELECT * FROM ({sql}) AS memory_program LIMIT ?", [task_id, max_rows])
            columns = [item[0] for item in cursor.description]
            rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
            if len(json.dumps(rows, default=str, ensure_ascii=False).encode()) > max_bytes:
                raise OverflowError("SQL result exceeds output budget")
            sender.send(("ok", rows))
    except BaseException as exc:
        sender.send((type(exc).__name__, str(exc)[:512]))
    finally:
        sender.close()


class MemoryProgram(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: int
    sql: str
    state: ProgramState
    content_hash: str
    created_at: datetime


def validate_relational_program(sql: str) -> str:
    # Exact source identity matters: whitespace inside a SQL literal is data.
    normalized = sql
    if normalized.count(":task_id") != 1 or "?" in normalized:
        raise ValueError("memory program must contain exactly one :task_id parameter")
    try:
        statements = duckdb.extract_statements(normalized.replace(":task_id", "?"))
    except duckdb.Error as exc:
        raise ValueError("memory program is not valid DuckDB SQL") from exc
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        raise ValueError("memory program must be a SELECT")
    if "ledger_events" not in normalized.casefold():
        raise ValueError("memory program must read ledger_events")
    return normalized


class ProgramCatalog:
    def __init__(self, database: Path) -> None:
        self.database = database
        with duckdb.connect(str(database)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_programs (
                    name VARCHAR NOT NULL,
                    version INTEGER NOT NULL,
                    sql VARCHAR NOT NULL,
                    state VARCHAR NOT NULL,
                    content_hash VARCHAR NOT NULL,
                    created_at VARCHAR NOT NULL,
                    PRIMARY KEY(name, version)
                )
                """
            )

    def register(self, name: str, version: int, sql: str) -> MemoryProgram:
        normalized = validate_relational_program(sql)
        digest = hashlib.sha256(normalized.encode()).hexdigest()
        created_at = datetime.now(UTC)
        with duckdb.connect(str(self.database)) as connection:
            connection.execute(
                "INSERT INTO memory_programs VALUES (?, ?, ?, 'candidate', ?, ?)",
                [name, version, normalized, digest, created_at.isoformat()],
            )
        return MemoryProgram(
            name=name,
            version=version,
            sql=normalized,
            state="candidate",
            content_hash=digest,
            created_at=created_at,
        )

    def transition(self, name: str, version: int, state: ProgramState) -> MemoryProgram:
        allowed: dict[ProgramState, set[ProgramState]] = {
            "candidate": {"shadow", "retired"},
            "shadow": {"active", "retired"},
            "active": {"retired"},
            "retired": set(),
        }
        current = self.get(name, version)
        if current is None:
            raise KeyError(f"unknown memory program: {name}@{version}")
        if state not in allowed[current.state]:
            raise ValueError(f"invalid memory program transition: {current.state} -> {state}")
        with duckdb.connect(str(self.database)) as connection:
            connection.execute(
                "UPDATE memory_programs SET state=? WHERE name=? AND version=?",
                [state, name, version],
            )
        return current.model_copy(update={"state": state})

    def get(self, name: str, version: int) -> MemoryProgram | None:
        with duckdb.connect(str(self.database)) as connection:
            row = connection.execute(
                "SELECT * FROM memory_programs WHERE name=? AND version=?",
                [name, version],
            ).fetchone()
        if row is None:
            return None
        return MemoryProgram(
            name=row[0],
            version=row[1],
            sql=row[2],
            state=row[3],
            content_hash=row[4],
            created_at=row[5],
        )

    def execute(
        self,
        name: str,
        version: int,
        *,
        task_id: str,
        max_rows: int = 1_000,
        max_bytes: int = 1_000_000,
        max_scan_events: int = 10_000,
        timeout_seconds: float = 5,
        watermark: int | None = None,
        recorded_before: datetime | None = None,
    ) -> list[dict[str, object]]:
        program = self.get(name, version)
        if program is None or program.state != "active":
            raise PermissionError("only active memory programs may affect retrieval")
        if not 1 <= max_rows <= _MAX_RESULT_ROWS:
            raise ValueError(f"max_rows must be between 1 and {_MAX_RESULT_ROWS}")
        if not 128 <= max_bytes <= 1_000_000 or not 1 <= max_scan_events <= 100_000:
            raise ValueError("SQL byte/scan budget is out of range")
        if not 0 < timeout_seconds <= 60 or (watermark is not None and watermark < 0):
            raise ValueError("invalid SQL deadline or watermark")
        if recorded_before is not None and recorded_before.utcoffset() is None:
            raise ValueError("SQL temporal boundary requires timezone")
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=_execute_bounded_sql, args=(
            sender, str(self.database), program.sql.replace(":task_id", "?"), task_id,
            max_rows, max_bytes, max_scan_events, watermark,
            recorded_before.isoformat() if recorded_before else None,
        ), daemon=True)
        process.start()
        sender.close()
        try:
            if not receiver.poll(timeout_seconds):
                raise TimeoutError("SQL context program deadline exceeded")
            kind, value = receiver.recv()
            if kind == "ok":
                return value
            if kind == "PermissionException":
                raise duckdb.PermissionException(value)
            if kind == "OverflowError":
                raise OverflowError(value)
            raise ValueError(f"SQL context program failed ({kind}): {value}")
        except EOFError as exc:
            raise RuntimeError("SQL worker exited without a result") from exc
        finally:
            receiver.close()
            if process.is_alive():
                process.terminate()
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join()
