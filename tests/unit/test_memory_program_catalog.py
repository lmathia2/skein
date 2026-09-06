from pathlib import Path

import duckdb
import pytest

from harness.ledger import DuckDbLedgerStore
from harness.memory.catalog import ProgramCatalog, validate_relational_program


def test_program_preserves_literal_whitespace_and_exact_source_hash(tmp_path: Path) -> None:
    import hashlib

    database = tmp_path / "ledger.duckdb"
    ledger = DuckDbLedgerStore(database)
    ledger.append(task_id="task", source="test", source_id="1", kind="safe")
    catalog = ProgramCatalog(database)
    sql = " SELECT 'two  spaces' AS text FROM ledger_events WHERE task_id=:task_id "
    program = catalog.register("literal", 1, sql)
    assert program.sql == sql
    assert program.content_hash == hashlib.sha256(sql.encode()).hexdigest()
    catalog.transition("literal", 1, "shadow")
    catalog.transition("literal", 1, "active")
    assert catalog.execute("literal", 1, task_id="task") == [{"text": "two  spaces"}]


def test_sql_worker_deadline_snapshot_and_output_limits(tmp_path: Path) -> None:
    import multiprocessing
    import time

    database = tmp_path / "ledger.duckdb"
    ledger = DuckDbLedgerStore(database)
    for i in range(3):
        ledger.append(task_id="task", source="test", source_id=str(i), kind="safe")
    catalog = ProgramCatalog(database)
    for name, sql in {
        "count": "SELECT count(*) AS n FROM ledger_events WHERE task_id=:task_id",
        "large": "SELECT repeat('x', 1000) AS text FROM ledger_events WHERE task_id=:task_id",
        "slow": "SELECT sum(i % 97) FROM range(1000000000000) r(i) WHERE :task_id = (SELECT task_id FROM ledger_events LIMIT 1)",
    }.items():
        catalog.register(name, 1, sql)
        catalog.transition(name, 1, "shadow")
        catalog.transition(name, 1, "active")
    assert catalog.execute("count", 1, task_id="task", watermark=2) == [{"n": 2}]
    with pytest.raises(OverflowError, match="scan"):
        catalog.execute("count", 1, task_id="task", max_scan_events=1)
    with pytest.raises(OverflowError, match="output"):
        catalog.execute("large", 1, task_id="task", max_bytes=128)
    children = {process.pid for process in multiprocessing.active_children()}
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        catalog.execute("slow", 1, task_id="task", timeout_seconds=0.5)
    assert time.monotonic() - start < 4
    assert {process.pid for process in multiprocessing.active_children()} <= children


def test_programs_require_replay_shadow_before_activation(tmp_path: Path) -> None:
    database = tmp_path / "ledger.duckdb"
    ledger = DuckDbLedgerStore(database)
    ledger.append(task_id="task", source="test", source_id="1", kind="failure", status="failed")
    catalog = ProgramCatalog(database)
    catalog.register(
        "failures",
        1,
        "SELECT kind, status FROM ledger_events WHERE task_id=:task_id AND status='failed'",
    )
    with pytest.raises(PermissionError):
        catalog.execute("failures", 1, task_id="task")
    catalog.transition("failures", 1, "shadow")
    catalog.transition("failures", 1, "active")
    assert catalog.execute("failures", 1, task_id="task") == [
        {"kind": "failure", "status": "failed"}
    ]
    catalog.transition("failures", 1, "retired")
    with pytest.raises(PermissionError):
        catalog.execute("failures", 1, task_id="task")


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM ledger_events",
        "SELECT * FROM ledger_events; DROP TABLE ledger_events",
        "SELECT * FROM secrets",
        "COPY ledger_events TO '/tmp/leak'",
        "SELECT * FROM ledger_events",
        "SELECT * FROM ledger_events WHERE task_id=:task_id OR task_id=:task_id",
        "SELECT * FROM ledger_events WHERE task_id=?",
        "SELECT ( FROM ledger_events WHERE task_id=:task_id",
    ],
)
def test_relational_program_validator_fails_closed(sql: str) -> None:
    with pytest.raises(ValueError):
        validate_relational_program(sql)


def test_active_program_is_task_scoped_isolated_and_bounded(tmp_path: Path) -> None:
    database = tmp_path / "ledger.duckdb"
    ledger = DuckDbLedgerStore(database)
    ledger.append(task_id="task", source="test", source_id="1", kind="safe", status="completed")
    ledger.append(task_id="other", source="test", source_id="2", kind="secret", status="completed")
    catalog = ProgramCatalog(database)
    catalog.register(
        "bounded",
        1,
        "SELECT kind FROM ledger_events, range(20) WHERE task_id=:task_id",
    )
    catalog.transition("bounded", 1, "shadow")
    catalog.transition("bounded", 1, "active")

    assert catalog.execute("bounded", 1, task_id="task", max_rows=3) == [
        {"kind": "safe"},
        {"kind": "safe"},
        {"kind": "safe"},
    ]

    catalog.register(
        "external",
        1,
        "SELECT * FROM ledger_events, read_csv('/etc/passwd') WHERE task_id=:task_id",
    )
    catalog.transition("external", 1, "shadow")
    catalog.transition("external", 1, "active")
    with pytest.raises(duckdb.PermissionException):
        catalog.execute("external", 1, task_id="task")
