"""事件时严格入窗：裁决同票、双口径出窗、并发互斥。

口径全局只贯彻一种（窗规 出窗口径 切换，全局生效）：
- 切流账册：出窗点记指纹，整票拒或进迟到匣，回刷拒，缓冲不涨；
- 双写两轨：出窗点进只读归档旁路，缓冲只含窗内点。
"""
from pathlib import Path
import os
import tempfile
import threading

import pytest

_tmp = tempfile.mkdtemp()
os.environ["数据目录"] = _tmp
os.environ["夹具目录"] = str(Path(__file__).resolve().parents[2] / "fixtures")

from app import seed
from app.services import 装载, 贯验, 目录, 校验, 夹具, 裁决
from app.modules import 事件时严格入窗 as 严格入窗


@pytest.fixture(autouse=True)
def _db():
    seed.初始化库()
    yield
    # 全局口径用后复位，别污染邻测
    目录.改窗规({"出窗口径": "切流账册", "切流处置": "进迟到匣"})


def 载夹具(名: str, 事件后缀: str = "", 运单后缀: str = "", 批次: str | None = None) -> dict:
    体 = 夹具.读取(名)
    for ev in 体["事件"]:
        ev["事件编号"] = ev["事件编号"] + 事件后缀
        if 运单后缀:
            ev["运单键"] = ev["运单键"] + 运单后缀
    if 批次:
        体["批次编号"] = 批次
    return 体


def 缓冲笔数() -> int:
    return len(目录.缓冲列表())


def 迟到笔数() -> int:
    return len(目录.迟到列表())


def test_窗内成片进缓冲_同票裁决():
    前 = 缓冲笔数()
    报告 = 装载.装一批(载夹具("窗内成片样例", 事件后缀="-严", 运单后缀="-严", 批次="严-窗内"))
    assert 报告["入缓冲"] == 3
    assert 报告["迟到"] == 0 and 报告["归档"] == 0
    assert 缓冲笔数() == 前 + 3
    窗号 = 目录.窗规()["当前窗号"]
    for 编号 in ("E-in-1-严", "E-in-2-严", "E-in-3-严"):
        决 = 裁决.裁决查(编号)
        assert 决 is not None, f"{编号} 缺裁决行"
        assert 决["命运"] == "入缓冲"
        assert 决["窗号"] == 窗号
        assert 决["形态标记"] == "切流账册"
        assert 决["valid"] is True


def test_出窗切流_缓冲不涨有拒因():
    目录.改窗规({"出窗口径": "切流账册", "切流处置": "进迟到匣"})
    前缓冲, 前迟到 = 缓冲笔数(), 迟到笔数()
    报告 = 装载.装一批(载夹具("出窗噪声样例", 批次="严-出窗-切流"))
    assert 报告["入缓冲"] == 0
    assert 报告["迟到"] == 3
    assert 缓冲笔数() == 前缓冲, "切流下出窗点不得涨缓冲"
    assert 迟到笔数() == 前迟到 + 3
    决 = 裁决.裁决查("E-out-1")
    assert 决["命运"] == "迟到"
    assert 决["拒因或归档键"] == "事件时出窗"
    assert 决["形态标记"] == "切流账册"
    assert 决["valid"] is True
    账 = [t for t in 裁决.账册列表() if t["事件编号"] == "E-out-1"]
    assert 账 and 账[0]["拒因"] == "事件时出窗", "账册须能回看拒因"


def test_出窗切流_整票拒():
    目录.改窗规({"出窗口径": "切流账册", "切流处置": "整票拒"})
    前缓冲, 前迟到 = 缓冲笔数(), 迟到笔数()
    报告 = 装载.装一批(载夹具("出窗噪声样例", 事件后缀="-整", 运单后缀="-整", 批次="严-整拒"))
    assert 报告["入缓冲"] == 0 and 报告["迟到"] == 0
    assert 报告["驳回"] == 3
    assert 缓冲笔数() == 前缓冲 and 迟到笔数() == 前迟到
    决 = 裁决.裁决查("E-out-1-整")
    assert 决["命运"] == "整票拒"
    assert "拒因" in 决 and 决["拒因或归档键"]
    assert 决["valid"] is True
    assert any(t["事件编号"] == "E-out-1-整" for t in 裁决.账册列表())


def test_出窗双写_缓冲不涨归档有行():
    目录.改窗规({"出窗口径": "双写两轨"})
    前缓冲, 前迟到 = 缓冲笔数(), 迟到笔数()
    前归档 = len(裁决.归档列表())
    报告 = 装载.装一批(载夹具("出窗噪声样例", 事件后缀="-双", 运单后缀="-双", 批次="严-出窗-双写"))
    assert 报告["入缓冲"] == 0 and 报告["迟到"] == 0
    assert 报告["归档"] == 3
    assert 缓冲笔数() == 前缓冲, "双写下出窗点不得涨缓冲"
    assert 迟到笔数() == 前迟到, "双写下不进迟到匣"
    assert len(裁决.归档列表()) == 前归档 + 3, "双写下归档须有行"
    决 = 裁决.裁决查("E-out-1-双")
    assert 决["命运"] == "归档"
    assert 决["形态标记"] == "双写两轨"
    assert 决["拒因或归档键"].startswith("档-"), "归档键须可回看"
    assert 决["valid"] is True
    观测 = {r["事件编号"]: r for r in 目录.观测列表()}
    assert 观测["E-out-1-双"]["命运"] == "已归档"


def test_出窗回刷_账册拒():
    目录.改窗规({"出窗口径": "切流账册", "切流处置": "进迟到匣"})
    一 = 装载.装一批(载夹具("出窗噪声样例", 事件后缀="-刷1", 运单后缀="-刷", 批次="严-回刷-一"))
    assert 一["迟到"] == 3
    前账 = len(裁决.账册列表())
    前缓冲, 前迟到 = 缓冲笔数(), 迟到笔数()
    二 = 装载.装一批(载夹具("出窗噪声样例", 事件后缀="-刷2", 运单后缀="-刷", 批次="严-回刷-二"))
    assert 二["驳回"] == 3 and 二["迟到"] == 0 and 二["入缓冲"] == 0
    assert len(裁决.账册列表()) == 前账, "回刷不得再记账"
    assert 缓冲笔数() == 前缓冲 and 迟到笔数() == 前迟到
    决 = 裁决.裁决查("E-out-1-刷2")
    assert 决["命运"] == "回刷拒"
    assert "回刷" in 决["拒因或归档键"]
    assert 决["valid"] is True


def test_同号并发不双行():
    体甲 = 载夹具("同号并发样例", 批次="严-并发-甲")
    体乙 = 载夹具("同号并发样例", 批次="严-并发-乙")
    结果: list[dict] = []
    异常: list[Exception] = []

    def 跑(体):
        try:
            结果.append(装载.装一批(体))
        except Exception as e:  # noqa: BLE001
            异常.append(e)

    线 = [threading.Thread(target=跑, args=(体甲,)), threading.Thread(target=跑, args=(体乙,))]
    for t in 线:
        t.start()
    for t in 线:
        t.join()
    assert not 异常, f"并发装载不得互踩: {异常}"
    assert len(结果) == 2
    assert sorted(r["入缓冲"] for r in 结果) == [0, 1], "同号并发须一胜一负"
    assert sorted(r["驳回"] for r in 结果) == [0, 1]
    行 = [r for r in 目录.缓冲列表() if r["事件编号"] == "E-conc-1"]
    assert len(行) == 1, "同号并发不得缓冲双行"
    决 = 裁决.裁决查("E-conc-1")
    assert 决 is not None and 决["命运"] == "入缓冲" and 决["valid"] is True


def test_处理时早晚不改窗归属():
    规 = 目录.窗规()
    窗外 = {"事件编号": "u1", "运单键": "运单-U1", "网点键": "网点-东站", "事件时": "2026-11-24T09:00:00Z", "处理时": "2026-11-24T00:30:00Z"}
    assert 严格入窗.decide_window(窗外, 规) != "入缓冲", "处理时在窗内也不得入窗"
    窗内 = {"事件编号": "u2", "运单键": "运单-U2", "网点键": "网点-东站", "事件时": "2026-11-24T00:30:00Z", "处理时": "2099-01-01T00:00:00Z"}
    assert 严格入窗.decide_window(窗内, 规) == "入缓冲", "处理时再晚也不改窗号"


def test_裁决只读接口字段():
    装载.装一批(载夹具("窗内成片样例", 事件后缀="-口", 运单后缀="-口", 批次="严-接口"))
    行 = [r for r in 裁决.裁决列表() if r["事件编号"] == "E-in-1-口"]
    assert 行, "裁决列表须能回读"
    决 = 行[0]
    for 键 in ("事件编号", "窗号", "命运", "拒因或归档键", "形态标记", "valid"):
        assert 键 in 决, f"裁决接口缺字段 {键}"


def test_裁决路由只读():
    from starlette.testclient import TestClient

    from app.main import app

    装载.装一批(载夹具("窗内成片样例", 事件后缀="-路", 运单后缀="-路", 批次="严-路由"))
    客 = TestClient(app)
    应 = 客.get("/api/入窗裁决")
    assert 应.status_code == 200
    行 = [r for r in 应.json() if r["事件编号"] == "E-in-1-路"]
    assert 行 and 行[0]["命运"] == "入缓冲"
    单 = 客.get("/api/入窗裁决/E-in-1-路")
    assert 单.status_code == 200
    assert 单.json()["窗号"] == 目录.窗规()["当前窗号"]
    档 = 客.get("/api/出窗归档")
    assert 档.status_code == 200
    账 = 客.get("/api/切流账册")
    assert 账.status_code == 200


def test_目录迟到缓冲观测同接一裁决():
    目录.改窗规({"出窗口径": "切流账册", "切流处置": "进迟到匣"})
    体 = {
        "批次编号": "严-同裁",
        "事件": [
            {"事件编号": "E-same-in", "运单键": "运单-S", "网点键": "网点-东站", "事件时": "2026-11-24T00:20:00Z"},
            {"事件编号": "E-same-out", "运单键": "运单-S", "网点键": "网点-西港", "事件时": "2026-11-24T04:20:00Z"},
        ],
    }
    装载.装一批(体)
    缓冲 = {r["事件编号"]: r for r in 目录.缓冲列表()}
    assert 缓冲["E-same-in"]["裁决形态"] == "切流账册"
    迟到 = {r["事件编号"]: r for r in 目录.迟到列表()}
    assert 迟到["E-same-out"]["裁决形态"] == "切流账册"
    assert 迟到["E-same-out"]["裁决拒因"] == "事件时出窗"
    观测 = {r["事件编号"]: r for r in 目录.观测列表()}
    assert 观测["E-same-in"]["命运"] == "已入缓冲"
    assert 观测["E-same-out"]["命运"] == "已迟到"
    assert 观测["E-same-out"]["裁决形态"] == "切流账册"
    assert 校验.复核裁决("E-same-in") == []
    assert 校验.复核裁决("E-same-out") == []


def test_贯验对齐_含裁决复核():
    装载.装一批(载夹具("窗内成片样例", 事件后缀="-贯", 运单后缀="-贯", 批次="严-贯验"))
    验 = 贯验.跑一次()
    assert 验["是否对齐"] is True
    assert 验["裁决违规"] == 0
