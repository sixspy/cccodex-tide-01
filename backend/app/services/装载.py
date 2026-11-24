"""观测点装载 → 入窗或迟到/归档。

硬约束：
- 读窗规、写缓冲、写迟到匣同一事务一次成败（BEGIN IMMEDIATE 起互斥）；
- 同号并发灌同一事件编号：INSERT OR IGNORE 幂等，不得缓冲双行；
- 入窗裁决只认事件时，处理时早晚不改窗归属（裁决服务同票落定）。
"""
from __future__ import annotations
import json
import uuid
from app.db import 连接
from app.seed import 此刻
from app.services import 校验, 目录, 裁决

_观测命运 = {"入缓冲": "已入缓冲", "迟到": "已迟到", "归档": "已归档"}


def 装一批(体: dict) -> dict:
    批次 = 体.get("批次编号") or f"潮批-{uuid.uuid4().hex[:8]}"
    事件 = 体.get("事件") or []
    报告 = {"批次编号": 批次, "入流": 0, "入缓冲": 0, "迟到": 0, "归档": 0, "驳回": 0, "明细": []}
    for e in 校验.校验批次(体):
        报告["明细"].append({"命运": "批次预检", "说明": e})
    conn = 连接()
    conn.isolation_level = None  # 手动事务：读窗规+写缓冲+写迟到匣一次成败
    try:
        conn.execute("BEGIN IMMEDIATE")  # 写锁互斥：两名同灌同号串行落定
        规 = 目录.窗规(conn)
        for ev in 事件:
            行错 = 校验.校验事件(ev)
            if 行错:
                报告["驳回"] += 1
                报告["明细"].append({"命运": "驳回-校验", "说明": ";".join(行错)})
                continue
            编号 = ev["事件编号"]
            处理 = 此刻()
            cur = conn.execute(
                """INSERT OR IGNORE INTO 观测点(事件编号,运单键,网点键,事件时,处理时,命运,批次编号)
                   VALUES (?,?,?,?,?,?,?)""",
                (编号, ev["运单键"], ev["网点键"], ev["事件时"], 处理, "已装载", 批次),
            )
            if cur.rowcount == 0:
                报告["驳回"] += 1
                报告["明细"].append({"命运": "驳回-幂等", "说明": 编号})
                continue
            报告["入流"] += 1
            决 = 裁决.裁并落(conn, ev, 规, 处理)
            conn.execute(
                "UPDATE 观测点 SET 命运=? WHERE 事件编号=?",
                (_观测命运.get(决["命运"], "已驳回"), 编号),
            )
            if 决["命运"] == "入缓冲":
                报告["入缓冲"] += 1
            elif 决["命运"] == "迟到":
                报告["迟到"] += 1
            elif 决["命运"] == "归档":
                报告["归档"] += 1
            else:
                报告["驳回"] += 1
            明细 = {"命运": 决["命运"], "事件编号": 编号, "窗号": 决["窗号"], "形态标记": 决["形态标记"]}
            if 决["拒因"]:
                明细["拒因"] = 决["拒因"]
            if 决["归档键"]:
                明细["归档键"] = 决["归档键"]
            报告["明细"].append(明细)
        conn.execute(
            """INSERT INTO 装载批次(编号,时刻,入流,入缓冲,迟到,报告) VALUES (?,?,?,?,?,?)""",
            (批次, 此刻(), 报告["入流"], 报告["入缓冲"], 报告["迟到"], json.dumps(报告, ensure_ascii=False)),
        )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()
    return 报告
