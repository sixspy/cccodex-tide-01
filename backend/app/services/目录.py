"""列表与窗规读写。"""
from __future__ import annotations
from app.db import 连接


def 窗规(conn=None) -> dict:
    """读窗规。装载同事务内须传 conn，与写缓冲/写迟到匣一次成败。"""
    if conn is not None:
        rows = conn.execute("SELECT 键,值 FROM 窗规").fetchall()
        return {r["键"]: r["值"] for r in rows}
    c = 连接()
    try:
        rows = c.execute("SELECT 键,值 FROM 窗规").fetchall()
        return {r["键"]: r["值"] for r in rows}
    finally:
        c.close()


def 改窗规(体: dict) -> dict:
    conn = 连接()
    try:
        for k, v in (体 or {}).items():
            conn.execute(
                "INSERT INTO 窗规(键,值) VALUES (?,?) ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
                (str(k), str(v)),
            )
        conn.commit()
        return 窗规()
    finally:
        conn.close()


def 网点列表() -> list[dict]:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 网点源 ORDER BY 源键").fetchall()]
    finally:
        conn.close()


def 观测列表() -> list[dict]:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute(
            """SELECT o.*, d.形态标记 AS 裁决形态, d.拒因 AS 裁决拒因, d.归档键 AS 裁决归档键
               FROM 观测点 o LEFT JOIN 入窗裁决 d ON d.事件编号=o.事件编号
               ORDER BY o.事件时 DESC LIMIT 200"""
        ).fetchall()]
    finally:
        conn.close()


def 缓冲列表() -> list[dict]:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute(
            """SELECT b.*, d.形态标记 AS 裁决形态
               FROM 入窗缓冲 b LEFT JOIN 入窗裁决 d ON d.事件编号=b.事件编号
               ORDER BY b.事件时"""
        ).fetchall()]
    finally:
        conn.close()


def 切片列表() -> list[dict]:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 窗切片账 ORDER BY 物化时 DESC LIMIT 200").fetchall()]
    finally:
        conn.close()


def 迟到列表() -> list[dict]:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute(
            """SELECT l.*, d.形态标记 AS 裁决形态, d.拒因 AS 裁决拒因
               FROM 迟到匣 l LEFT JOIN 入窗裁决 d ON d.事件编号=l.事件编号
               ORDER BY l.编号 DESC LIMIT 200"""
        ).fetchall()]
    finally:
        conn.close()


def 贯验列表() -> list[dict]:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 窗贯验 ORDER BY 编号 DESC LIMIT 50").fetchall()]
    finally:
        conn.close()


def 概览() -> dict:
    conn = 连接()
    try:
        return {
            "观测点": conn.execute("SELECT COUNT(*) AS c FROM 观测点").fetchone()["c"],
            "在缓冲": conn.execute("SELECT COUNT(*) AS c FROM 入窗缓冲 WHERE 命运='在缓冲'").fetchone()["c"],
            "窗切片": conn.execute("SELECT COUNT(*) AS c FROM 窗切片账").fetchone()["c"],
            "迟到": conn.execute("SELECT COUNT(*) AS c FROM 迟到匣").fetchone()["c"],
            "归档": conn.execute("SELECT COUNT(*) AS c FROM 出窗归档").fetchone()["c"],
            "裁决": conn.execute("SELECT COUNT(*) AS c FROM 入窗裁决").fetchone()["c"],
            "窗规": 窗规(),
        }
    finally:
        conn.close()
