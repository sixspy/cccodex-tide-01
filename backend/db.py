import os
import time

import psycopg
from psycopg.rows import dict_row

SCHEMA = """
CREATE TABLE IF NOT EXISTS intake_batches (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    status TEXT NOT NULL,
    accepted_count INTEGER NOT NULL,
    rejected_count INTEGER NOT NULL,
    disposition TEXT,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS landed_rows (
    id BIGSERIAL PRIMARY KEY,
    batch_id BIGINT NOT NULL REFERENCES intake_batches(id),
    line_no INTEGER NOT NULL,
    station_code TEXT NOT NULL,
    observed_at TIMESTAMP NOT NULL,
    tide_cm INTEGER NOT NULL,
    quality_flag TEXT NOT NULL,
    unregistered BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (station_code, observed_at)
);

CREATE TABLE IF NOT EXISTS reject_rows (
    id BIGSERIAL PRIMARY KEY,
    batch_id BIGINT NOT NULL REFERENCES intake_batches(id),
    line_no INTEGER NOT NULL,
    raw_line TEXT NOT NULL,
    reason TEXT NOT NULL,
    unregistered BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS station_catalog (
    station_code TEXT PRIMARY KEY,
    note TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS catalog_settings (
    id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    disposition TEXT NOT NULL,
    set_by TEXT NOT NULL,
    set_on DATE NOT NULL DEFAULT CURRENT_DATE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS batch_stations (
    batch_id BIGINT NOT NULL REFERENCES intake_batches(id),
    station_code TEXT NOT NULL,
    PRIMARY KEY (batch_id, station_code)
);
"""

# 老库升级：补列、回填批次站码。新库由 SCHEMA 一次建全，这些语句全部空转。
MIGRATE = """
ALTER TABLE intake_batches ADD COLUMN IF NOT EXISTS disposition TEXT;
ALTER TABLE landed_rows ADD COLUMN IF NOT EXISTS unregistered BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE reject_rows ADD COLUMN IF NOT EXISTS unregistered BOOLEAN NOT NULL DEFAULT FALSE;
INSERT INTO batch_stations (batch_id, station_code)
SELECT DISTINCT batch_id, station_code FROM landed_rows
ON CONFLICT DO NOTHING;
"""

DISPOSITIONS = {"block": "挡在未接纳", "land_and_mark": "先落库再标未入册"}
DEFAULT_DISPOSITION = "block"

SEED_CATALOG = ("WG01", "BG02")

SEED_CLEAN = """吴淞口 11-12 早班
WG01|202611120830|186|A
WG01|202611120900|191|A
BG02|202611120830|142|M
"""

SEED_BROKEN = """外高桥 11-12 错报
LG04|202611121000|205|A
WG01|202611120830|186|A
WG0X|202611121000|10|A
"""


def connect():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def init_db():
    last_error = None
    for _ in range(30):
        try:
            with connect() as conn:
                conn.execute(SCHEMA)
                conn.execute(MIGRATE)
                count = conn.execute("SELECT COUNT(*) AS n FROM intake_batches").fetchone()["n"]
                if count == 0:
                    _seed(conn)
                conn.commit()
            return
        except psycopg.OperationalError as exc:
            last_error = exc
            time.sleep(1)
    raise last_error


def _lock_key(station_code: str) -> str:
    return f"tide-catalog:{station_code}"


def lock_station_for_save(conn, station_code: str):
    """目录保存事务调用：排他锁，事务提交前落库侧进不来。"""
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (_lock_key(station_code),),
    )


def try_lock_station(conn, station_code: str) -> bool:
    """落库侧调用：共享锁，批次之间互不挡；只有目录保存（排他）握着锁时才拿不到。"""
    row = conn.execute(
        "SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s, 0)) AS got",
        (_lock_key(station_code),),
    ).fetchone()
    return row["got"]


def read_catalog(conn):
    settings = conn.execute(
        """
        SELECT disposition, set_by, set_on, (set_on = CURRENT_DATE) AS locked_today
        FROM catalog_settings WHERE id = 1
        """
    ).fetchone()
    entries = conn.execute(
        """
        SELECT station_code, note, created_by, created_at
        FROM station_catalog ORDER BY station_code
        """
    ).fetchall()
    return settings, entries


def current_disposition(settings) -> str:
    return settings["disposition"] if settings else DEFAULT_DISPOSITION


def _insert_batch(conn, title: str, raw_text: str, created_by: str, brought_disposition: str | None = None):
    from parser import parse_telegram_line

    settings, _ = read_catalog(conn)
    disposition = current_disposition(settings)
    # 贴入时带来的去向和目录设置不一致，未入册的行停住并记「目录去向不符」
    disposition_mismatch = brought_disposition is not None and brought_disposition != disposition

    batch = conn.execute(
        """
        INSERT INTO intake_batches (title, raw_text, status, accepted_count, rejected_count, created_by)
        VALUES (%s, %s, '处理中', 0, 0, %s)
        RETURNING id
        """,
        (title, raw_text, created_by),
    ).fetchone()
    accepted = 0
    rejected = 0
    seen = set()
    station_state = {}

    def reject(line_no, raw_line, reason, unregistered=False):
        nonlocal rejected
        conn.execute(
            """
            INSERT INTO reject_rows (batch_id, line_no, raw_line, reason, unregistered)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (batch["id"], line_no, raw_line.strip(), reason, unregistered),
        )
        rejected += 1

    def state_of(station_code):
        if station_code not in station_state:
            if not try_lock_station(conn, station_code):
                station_state[station_code] = "saving"
            else:
                in_catalog = conn.execute(
                    "SELECT 1 FROM station_catalog WHERE station_code = %s",
                    (station_code,),
                ).fetchone()
                station_state[station_code] = "registered" if in_catalog else "unregistered"
        return station_state[station_code]

    for line_no, raw_line in enumerate(raw_text.splitlines(), start=1):
        if not raw_line.strip():
            continue
        parsed, reason = parse_telegram_line(raw_line)
        if reason:
            reject(line_no, raw_line, reason)
            continue
        station_code = parsed["station_code"]
        conn.execute(
            "INSERT INTO batch_stations (batch_id, station_code) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (batch["id"], station_code),
        )
        state = state_of(station_code)
        if state == "saving":
            reject(line_no, raw_line, "目录保存中")
            continue
        unregistered = state == "unregistered"
        if unregistered:
            if disposition_mismatch:
                reject(line_no, raw_line, "目录去向不符", unregistered=True)
                continue
            if disposition == "block":
                reject(line_no, raw_line, "站码未入册", unregistered=True)
                continue
        key = (station_code, parsed["observed_at"])
        if key in seen:
            reason = "同一批里观测时刻重复"
        else:
            existing = conn.execute(
                "SELECT id FROM landed_rows WHERE station_code = %s AND observed_at = %s",
                key,
            ).fetchone()
            reason = "这个站这个时刻已经落过库" if existing else None
        if reason:
            reject(line_no, raw_line, reason, unregistered=unregistered)
            continue
        seen.add(key)
        conn.execute(
            """
            INSERT INTO landed_rows (batch_id, line_no, station_code, observed_at, tide_cm, quality_flag, unregistered)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                batch["id"],
                line_no,
                station_code,
                parsed["observed_at"],
                parsed["tide_cm"],
                parsed["quality_flag"],
                unregistered,
            ),
        )
        accepted += 1
    if accepted and not rejected:
        status = "已归集"
    elif accepted:
        status = "部分接纳"
    else:
        status = "未接纳"
    conn.execute(
        """
        UPDATE intake_batches
        SET status = %s, accepted_count = %s, rejected_count = %s, disposition = %s
        WHERE id = %s
        """,
        (status, accepted, rejected, disposition, batch["id"]),
    )
    return batch["id"]


def _seed(conn):
    for code in SEED_CATALOG:
        conn.execute(
            "INSERT INTO station_catalog (station_code, note, created_by) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (code, "种子站", "admin"),
        )
    title, _, body = SEED_CLEAN.partition("\n")
    _insert_batch(conn, title, body.strip(), "admin")
    title, _, body = SEED_BROKEN.partition("\n")
    _insert_batch(conn, title, body.strip(), "admin")
