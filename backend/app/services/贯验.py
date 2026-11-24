"""窗贯验：缓冲/切片/迟到互证，外加裁决同票复核。"""
from __future__ import annotations
from app.db import 连接
from app.seed import 此刻
from app.services import 校验


def 跑一次() -> dict:
    conn = 连接()
    try:
        缓冲 = conn.execute(
            "SELECT COUNT(*) AS c FROM 入窗缓冲 WHERE 命运='在缓冲'"
        ).fetchone()["c"]
        已物化缓冲 = conn.execute(
            "SELECT COUNT(*) AS c FROM 入窗缓冲 WHERE 命运='已物化'"
        ).fetchone()["c"]
        切片事件 = conn.execute(
            "SELECT COALESCE(SUM(事件数),0) AS c FROM 窗切片账"
        ).fetchone()["c"]
        迟到 = conn.execute("SELECT COUNT(*) AS c FROM 迟到匣").fetchone()["c"]
        # 基线口径：已物化缓冲事件数应等于切片事件数之和
        切片对齐 = int(已物化缓冲) == int(切片事件)
        # 裁决同票复核：每笔裁决的缓冲/迟到匣/归档/账册行须同票落定
        裁决笔数 = conn.execute("SELECT COUNT(*) AS c FROM 入窗裁决").fetchone()["c"]
        违规 = 0
        for r in conn.execute("SELECT 事件编号 FROM 入窗裁决").fetchall():
            if 校验.复核裁决(r["事件编号"], conn):
                违规 += 1
        对齐 = 1 if (切片对齐 and 违规 == 0) else 0
        说明 = "缓冲物化与切片事件数一致" if 切片对齐 else f"撕裂：已物化缓冲={已物化缓冲} 切片事件={切片事件}"
        说明 += f"；裁决{裁决笔数}笔复核违规{违规}笔"
        时刻 = 此刻()
        conn.execute(
            """INSERT INTO 窗贯验(时刻,缓冲笔数,切片笔数,迟到笔数,是否对齐,说明)
               VALUES (?,?,?,?,?,?)""",
            (时刻, 缓冲, int(切片事件), 迟到, 对齐, 说明),
        )
        conn.commit()
        return {
            "时刻": 时刻,
            "在缓冲": 缓冲,
            "已物化缓冲": int(已物化缓冲),
            "切片事件": int(切片事件),
            "迟到": 迟到,
            "裁决笔数": 裁决笔数,
            "裁决违规": 违规,
            "是否对齐": bool(对齐),
            "说明": 说明,
        }
    finally:
        conn.close()
