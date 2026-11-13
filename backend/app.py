from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from auth import authenticate, issue_token, read_user
from db import (
    DISPOSITIONS,
    connect,
    current_disposition,
    init_db,
    lock_station_for_save,
    read_catalog,
)
from parser import FLAGS, STATION


class LoginBody(BaseModel):
    username: str
    password: str


class BatchBody(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    raw_text: str = Field(min_length=1)
    disposition: str | None = None


class SettingsBody(BaseModel):
    disposition: str


class EntryBody(BaseModel):
    station_code: str
    note: str = ""


def _stamp(value):
    return value.isoformat(sep=" ", timespec="minutes") if value else None


def _catalog_payload(conn):
    settings, entries = read_catalog(conn)
    for entry in entries:
        entry["created_at"] = _stamp(entry["created_at"])
    effective = current_disposition(settings)
    return {
        "settings": settings
        and {
            "disposition": settings["disposition"],
            "disposition_name": DISPOSITIONS[settings["disposition"]],
            "set_by": settings["set_by"],
            "set_on": settings["set_on"].isoformat(),
            "locked_today": settings["locked_today"],
        },
        "effective_disposition": effective,
        "effective_disposition_name": DISPOSITIONS[effective],
        "entries": entries,
    }


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(lifespan=lifespan)


def _user_or_401(authorization: str | None):
    user = read_user(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    return user


def _writer_or_403(user):
    if user["role"] != "writer":
        raise HTTPException(status_code=403, detail="档案员只能翻目录和报文，不能改")


@app.get("/api/health")
def health():
    with connect() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok", "service": "tide-telegram-intake"}


@app.post("/api/login")
def login(body: LoginBody):
    user = authenticate(body.username.strip(), body.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {
        "access_token": issue_token(user),
        "username": user["username"],
        "role": user["role"],
        "title": user["title"],
    }


@app.get("/api/catalog")
def get_catalog(authorization: str | None = Header(default=None)):
    _user_or_401(authorization)
    with connect() as conn:
        return _catalog_payload(conn)


@app.post("/api/catalog/settings")
def set_disposition(body: SettingsBody, authorization: str | None = Header(default=None)):
    user = _user_or_401(authorization)
    _writer_or_403(user)
    if body.disposition not in DISPOSITIONS:
        raise HTTPException(status_code=400, detail="去向只能是挡在未接纳或先落库再标未入册")
    with connect() as conn:
        # 去向当天只许定一次：WHERE 条件让并发下的第二个人原地落空
        result = conn.execute(
            """
            INSERT INTO catalog_settings (id, disposition, set_by, set_on, updated_at)
            VALUES (1, %s, %s, CURRENT_DATE, NOW())
            ON CONFLICT (id) DO UPDATE
            SET disposition = EXCLUDED.disposition,
                set_by = EXCLUDED.set_by,
                set_on = EXCLUDED.set_on,
                updated_at = EXCLUDED.updated_at
            WHERE catalog_settings.set_on < CURRENT_DATE
            """,
            (body.disposition, user["username"]),
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=409, detail="去向当天已选定，明天才能改")
        conn.commit()
        return _catalog_payload(conn)


@app.post("/api/catalog/entries")
def add_catalog_entry(body: EntryBody, authorization: str | None = Header(default=None)):
    user = _user_or_401(authorization)
    _writer_or_403(user)
    code = body.station_code.strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="空编码不能写进目录")
    if not STATION.match(code):
        raise HTTPException(status_code=400, detail="站码应为两位字母加两位数字")
    with connect() as conn:
        # 保存期间握住这个站码的锁，落库侧在提交前都进不来
        lock_station_for_save(conn, code)
        result = conn.execute(
            """
            INSERT INTO station_catalog (station_code, note, created_by)
            VALUES (%s, %s, %s)
            ON CONFLICT (station_code) DO NOTHING
            """,
            (code, body.note.strip(), user["username"]),
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=409, detail="目录争用：这个站码别人先一步写进了目录")
        conn.commit()
        return _catalog_payload(conn)


@app.get("/api/batches")
def list_batches(authorization: str | None = Header(default=None)):
    _user_or_401(authorization)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                b.id, b.title, b.status, b.accepted_count, b.rejected_count,
                b.disposition, b.created_by, b.created_at,
                COALESCE(
                    (SELECT json_agg(DISTINCT bs.station_code ORDER BY bs.station_code)
                     FROM batch_stations bs WHERE bs.batch_id = b.id),
                    '[]'
                ) AS station_codes,
                (SELECT COUNT(*) FROM landed_rows l WHERE l.batch_id = b.id AND l.unregistered)
              + (SELECT COUNT(*) FROM reject_rows r WHERE r.batch_id = b.id AND r.unregistered)
                AS unregistered_count
            FROM intake_batches b
            ORDER BY b.id DESC
            """
        ).fetchall()
    for row in rows:
        row["created_at"] = _stamp(row["created_at"])
        row["disposition_name"] = DISPOSITIONS.get(row["disposition"], "—")
    return rows


@app.get("/api/batches/{batch_id}")
def batch_detail(batch_id: int, authorization: str | None = Header(default=None)):
    _user_or_401(authorization)
    with connect() as conn:
        batch = conn.execute(
            """
            SELECT id, title, raw_text, status, accepted_count, rejected_count,
                   disposition, created_by, created_at
            FROM intake_batches WHERE id = %s
            """,
            (batch_id,),
        ).fetchone()
        if not batch:
            raise HTTPException(status_code=404, detail="没有这一批报文")
        landed = conn.execute(
            """
            SELECT line_no, station_code, observed_at, tide_cm, quality_flag, unregistered
            FROM landed_rows WHERE batch_id = %s ORDER BY line_no
            """,
            (batch_id,),
        ).fetchall()
        rejected = conn.execute(
            """
            SELECT line_no, raw_line, reason, unregistered
            FROM reject_rows WHERE batch_id = %s ORDER BY line_no
            """,
            (batch_id,),
        ).fetchall()
    batch["created_at"] = _stamp(batch["created_at"])
    batch["disposition_name"] = DISPOSITIONS.get(batch["disposition"], "—")
    for row in landed:
        row["observed_at"] = _stamp(row["observed_at"])
        row["flag_label"] = FLAGS.get(row["quality_flag"], row["quality_flag"])
        row["unregistered_reason"] = "站码未入册，按去向先落库" if row["unregistered"] else None
    return {"batch": batch, "landed": landed, "rejected": rejected}


@app.post("/api/batches")
def create_batch(body: BatchBody, authorization: str | None = Header(default=None)):
    user = _user_or_401(authorization)
    _writer_or_403(user)
    from db import _insert_batch

    title = body.title.strip()
    raw_text = body.raw_text.strip()
    if not title or not raw_text:
        raise HTTPException(status_code=400, detail="标题和报文都不能空")
    if body.disposition is not None and body.disposition not in DISPOSITIONS:
        raise HTTPException(status_code=400, detail="去向只能是挡在未接纳或先落库再标未入册")
    with connect() as conn:
        batch_id = _insert_batch(conn, title, raw_text, user["username"], body.disposition)
        conn.commit()
    return batch_detail(batch_id, authorization)


if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
