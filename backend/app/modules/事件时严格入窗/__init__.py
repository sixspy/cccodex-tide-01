"""事件时严格入窗：只按事件时裁决入窗，处理时早晚不改窗归属。

已接线（不再是空桩）：
- 装载服务经 app.services.裁决.裁并落 调本模块判定，读窗规/写缓冲/写迟到匣同一事务；
- 出窗两种口径全局只贯彻一种（窗规 出窗口径 切换）：
  · 切流账册：出窗点记指纹，整票拒或进迟到匣，回刷拒，缓冲不涨；
  · 双写两轨：出窗点进只读归档旁路，现行缓冲只含窗内点。
- 页面可读：当前策略名（窗规策略）、命运变化（观测流/入窗缓冲/迟到匣）、可点下一步（潮切台）。
"""
from __future__ import annotations

import hashlib

from app.services.时间 import 在窗内

NAME = "事件时严格入窗"
CONTRACT = "只按事件时判定是否入当前窗，处理时再晚也不改窗号"

口径_切流 = "切流账册"
口径_双写 = "双写两轨"
处置_迟到 = "进迟到匣"
处置_整拒 = "整票拒"

HOOKS = (
    "before_load",
    "after_load",
    "decide_window",
    "before_close",
    "after_close",
    "extra_verify_checks",
)


def describe() -> dict:
    return {
        "空桩": NAME,
        "契约": CONTRACT,
        "钩子": list(HOOKS),
        "默认启用": True,
        "说明": "已接线：装载/校验/目录/迟到匣/入窗缓冲同接一个裁决",
    }


def 口径(规: dict) -> str:
    """全局只贯彻一种正确性口径；缺省/乱值回落切流账册。"""
    v = str((规 or {}).get("出窗口径") or "").strip()
    return v if v in (口径_切流, 口径_双写) else 口径_切流


def 切流处置(规: dict) -> str:
    """切流账册下出窗点的处置：进迟到匣（默认）或整票拒。"""
    v = str((规 or {}).get("切流处置") or "").strip()
    return v if v == 处置_整拒 else 处置_迟到


def 出窗指纹(ev: dict) -> str:
    """出窗指纹：运单/网点/事件时内容哈希，回刷同内容即拒。"""
    料 = "|".join(str(ev.get(k) or "") for k in ("运单键", "网点键", "事件时"))
    return hashlib.sha1(料.encode("utf-8")).hexdigest()[:16]


def decide_window(event: dict, rules: dict) -> str:
    """只按事件时裁决：入缓冲 / 迟到 / 归档 / 整票拒。处理时不参与窗号判定。"""
    规 = rules or {}
    事件时 = str(event.get("事件时") or "")
    if 在窗内(事件时, str(规.get("窗起") or ""), str(规.get("窗止") or "")):
        return "入缓冲"
    if 口径(规) == 口径_双写:
        return "归档"
    return "整票拒" if 切流处置(规) == 处置_整拒 else "迟到"


def before_load(batch: dict) -> dict:
    """装载前钩子：默认透传。"""
    return batch


def after_load(report: dict) -> dict:
    """装载后钩子：默认透传。"""
    return report


def before_close(window_id: str) -> None:
    """关窗前钩子：默认不阻断。"""
    return None


def after_close(report: dict) -> dict:
    """关窗后钩子：默认透传。"""
    return report


def extra_verify_checks(snapshot: dict) -> list[str]:
    """附加贯验检查：基线无额外项。"""
    return []
