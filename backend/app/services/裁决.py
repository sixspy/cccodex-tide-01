"""入窗裁决：唯一裁决口径，装载/校验/目录/迟到匣/入窗缓冲同接此一裁决。

同票落定：裁决行与缓冲行/迟到匣行/归档行/账册行在同一事务内一次成败；
调用方（装载）负责开事务并先读窗规，本模块只在该事务内写。
"""
from __future__ import annotations

import uuid

from app.db import 连接
from app.modules import 事件时严格入窗 as 严格


def 裁并落(conn, ev: dict, 规: dict, 处理时: str) -> dict:
    """同一事务内裁决并落定。只按事件时定窗归属，处理时不参与。"""
    编号 = str(ev["事件编号"])
    形态 = 严格.口径(规)
    走向 = 严格.decide_window(ev, 规)
    决 = {
        "事件编号": 编号,
        "窗号": str(规.get("当前窗号") or "W-demo"),
        "命运": 走向,
        "拒因": "",
        "归档键": "",
        "形态标记": 形态,
        "指纹": "",
    }
    if 走向 == "入缓冲":
        conn.execute(
            """INSERT INTO 入窗缓冲(事件编号,运单键,网点键,事件时,窗号,命运,入缓冲时)
               VALUES (?,?,?,?,?,?,?)""",
            (编号, ev["运单键"], ev["网点键"], ev["事件时"], 决["窗号"], "在缓冲", 处理时),
        )
    elif 走向 == "归档":
        # 双写两轨：出窗点进只读归档旁路，缓冲集合只含窗内点
        决["窗号"] = "窗外"
        决["拒因"] = "事件时出窗"
        决["归档键"] = f"档-{编号}-{uuid.uuid4().hex[:6]}"
        conn.execute(
            """INSERT INTO 出窗归档(归档键,事件编号,运单键,网点键,事件时,归档时,形态标记)
               VALUES (?,?,?,?,?,?,?)""",
            (决["归档键"], 编号, ev["运单键"], ev["网点键"], ev["事件时"], 处理时, 形态),
        )
    else:
        # 切流账册：出窗点记指纹，整票拒或进迟到匣，回刷同内容即拒
        决["窗号"] = "窗外"
        决["拒因"] = "事件时出窗"
        决["指纹"] = 严格.出窗指纹(ev)
        处置 = 严格.切流处置(规)
        cur = conn.execute(
            """INSERT OR IGNORE INTO 切流账册(指纹,事件编号,运单键,事件时,拒因,处置,记账时)
               VALUES (?,?,?,?,?,?,?)""",
            (决["指纹"], 编号, ev["运单键"], ev["事件时"], "事件时出窗", 处置, 处理时),
        )
        if cur.rowcount == 0:
            决["命运"] = "回刷拒"
            决["拒因"] = "出窗回刷·账册已有指纹"
        elif 走向 == "整票拒":
            决["命运"] = "整票拒"
            决["拒因"] = "事件时出窗·整票拒"
        else:
            决["命运"] = "迟到"
            conn.execute(
                """INSERT INTO 迟到匣(事件编号,运单键,原因,事件时,入匣时)
                   VALUES (?,?,?,?,?)""",
                (编号, ev["运单键"], "事件时出窗", ev["事件时"], 处理时),
            )
    conn.execute(
        """INSERT INTO 入窗裁决(事件编号,窗号,命运,拒因,归档键,形态标记,指纹,裁决时)
           VALUES (?,?,?,?,?,?,?,?)""",
        (决["事件编号"], 决["窗号"], 决["命运"], 决["拒因"], 决["归档键"], 决["形态标记"], 决["指纹"], 处理时),
    )
    return 决


_裁决查询 = """
SELECT d.事件编号, d.窗号, d.命运, d.拒因, d.归档键, d.形态标记, d.指纹, d.裁决时,
  CASE
    WHEN d.命运='入缓冲' THEN b.事件编号 IS NOT NULL
    WHEN d.命运='迟到' THEN (l.编号 IS NOT NULL AND t.指纹 IS NOT NULL)
    WHEN d.命运='归档' THEN a.归档键 IS NOT NULL
    WHEN d.命运 IN ('整票拒','回刷拒') THEN t.指纹 IS NOT NULL
    ELSE 0
  END AS 落定
FROM 入窗裁决 d
LEFT JOIN 入窗缓冲 b ON b.事件编号 = d.事件编号
LEFT JOIN 迟到匣 l ON l.事件编号 = d.事件编号
LEFT JOIN 出窗归档 a ON a.事件编号 = d.事件编号
LEFT JOIN 切流账册 t ON t.指纹 = d.指纹 AND d.指纹 != ''
"""


def _成形(r) -> dict:
    return {
        "事件编号": r["事件编号"],
        "窗号": r["窗号"],
        "命运": r["命运"],
        "拒因或归档键": r["归档键"] if r["命运"] == "归档" else r["拒因"],
        "形态标记": r["形态标记"],
        "valid": bool(r["落定"]),
        "拒因": r["拒因"],
        "归档键": r["归档键"],
        "裁决时": r["裁决时"],
    }


def 裁决列表(限制: int = 300) -> list[dict]:
    """入窗裁决只读接口：事件编号/窗号/命运/拒因或归档键/形态标记/valid。"""
    conn = 连接()
    try:
        rows = conn.execute(
            _裁决查询 + " ORDER BY d.裁决时 DESC, d.事件编号 DESC LIMIT ?", (限制,)
        ).fetchall()
        return [_成形(r) for r in rows]
    finally:
        conn.close()


def 裁决查(事件编号: str) -> dict | None:
    conn = 连接()
    try:
        r = conn.execute(_裁决查询 + " WHERE d.事件编号=?", (事件编号,)).fetchone()
        return _成形(r) if r else None
    finally:
        conn.close()


def 归档列表() -> list[dict]:
    """只读归档旁路（双写两轨）：只查不写。"""
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 出窗归档 ORDER BY 归档时 DESC, 归档键 DESC LIMIT 200").fetchall()]
    finally:
        conn.close()


def 账册列表() -> list[dict]:
    """切流账册：出窗指纹与拒因可回看。"""
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 切流账册 ORDER BY 记账时 DESC, 指纹 DESC LIMIT 200").fetchall()]
    finally:
        conn.close()
