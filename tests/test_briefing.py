import asyncio
import json
import urllib.request

from app.bot.card_builder import build_morning_briefing_card
from app.commands import router
from app.models import briefing_repo
from app.scheduler import jobs
from app.services import briefing_service
from app.services.briefing_fetcher import BriefingNotUpdatedError
from app.services.briefing_html import render_market_map_html
from app.services.notifier import RecordingNotifier
from app.web.briefing_server import start_briefing_http_server


def run(coro):
    return asyncio.run(coro)


SAMPLE_STRUCTURED = {
    "headline_summary": "美债收益率回落提振科技股，半导体涨价潮延续，国内政策聚焦内需修复",
    "events_table": [
        {
            "event": "台积电上调全年资本开支指引",
            "asset": "半导体/AI算力",
            "direction": "涨",
            "logic": "AI算力需求超预期带动晶圆代工与设备链景气上行",
        },
        {
            "event": "中东停火谈判再生波折",
            "asset": "原油/黄金",
            "direction": "分化",
            "logic": "避险情绪支撑金价，但需求担忧压制油价反弹空间",
        },
    ],
    "causal_chains": [
        {
            "title": "AI资本开支扩张传导链",
            "category": "AI capex→硬件/信用层",
            "status": "市场共识",
            "flow_nodes": ["云厂商上调AI Capex", "CoWoS先进封装紧缺", "先进制程提价", "算力硬件与光模块领涨"],
            "jargon_plain": [
                {"term": "Capex（资本开支）", "explanation": "企业买服务器、建数据中心花的硬钱，是算力景气度的先行指标。"},
                {"term": "CoWoS", "explanation": "把AI芯片和高带宽内存封装在一起的关键技术，目前产能最紧缺的环节。"},
            ],
        }
    ],
    "verification_list": [
        {
            "topic": "半导体涨价能否顺利向终端传导",
            "watch_variables": ["北美四大云厂商下季Capex指引", "DRAM/NAND现货周度报价", "消费电子终端出货量"],
            "historical_analogy": {
                "event": "2017-2018年存储芯片超级周期",
                "lesson": "上游涨价初期股价弹性最大，但一旦终端需求负反馈出现，库存周期反转极快。",
                "difference": "本轮由企业级AI刚需驱动而非单纯手机换机周期，头部客户现金流更充裕。",
            },
        }
    ],
}


def test_subscription_lifecycle_and_respect_opt_out(tmp_db):
    # 首次执行 Q/B/S 自动订阅
    briefing_repo.ensure_subscribed("oc_user1", "p2p")
    assert briefing_repo.is_subscribed("oc_user1")
    assert [x["chat_id"] for x in briefing_repo.list_enabled_subscribers()] == ["oc_user1"]

    # 用户通过自然语言关闭早报后, 再次执行 Q/B/S 不会强行重新打开
    out_off = run(router.handle_async("关闭早报", {"chat_id": "oc_user1", "chat_type": "p2p"}))
    assert "关闭" in out_off and not briefing_repo.is_subscribed("oc_user1")
    briefing_repo.ensure_subscribed("oc_user1", "p2p")
    assert not briefing_repo.is_subscribed("oc_user1")

    # 用户主动重新开启
    out_on = run(router.handle_async("开启早报", {"chat_id": "oc_user1", "chat_type": "p2p"}))
    assert "开启" in out_on and briefing_repo.is_subscribed("oc_user1")


def test_dual_persistence_and_restore_after_disk_delete(tmp_db):
    html_doc = render_market_map_html(
        "2026-10-17",
        "华尔街见闻早餐FM-Radio | 2026年10月17日",
        "https://wallstreetcn.com/articles/3783821",
        "2026-10-17 07:09",
        SAMPLE_STRUCTURED,
    )
    assert "2026-10-17 早餐 · 全球市场可视化地图" in html_doc
    assert "AI capex→硬件/信用层" in html_doc
    assert "@media (max-width: 768px)" in html_doc

    rec = briefing_repo.save_briefing(
        "2026-10-17", "3783821", "华尔街见闻早餐FM-Radio | 2026年10月17日",
        "https://wallstreetcn.com/articles/3783821", "2026-10-17 07:09",
        SAMPLE_STRUCTURED, html_doc,
    )
    f_path = briefing_repo.html_file_path("2026-10-17")
    assert f_path.is_file()

    # 模拟磁盘静态文件被清理/丢失, get_or_restore_html 自动从 SQLite 恢复并写回磁盘
    f_path.unlink()
    assert not f_path.is_file()
    restored = briefing_repo.get_or_restore_html("2026-10-17")
    assert restored == html_doc and f_path.is_file()

    # 验证飞书 Schema 2.0 早报卡片结构与标题
    card = build_morning_briefing_card(
        rec, "http://8.153.91.185/briefings/2026-10-17", "http://8.153.91.185/briefings"
    )["card"]
    assert card["title"] == "2026-10-17 早餐"
    raw_str = json.dumps(card["raw_card"], ensure_ascii=False)
    assert "http://8.153.91.185/briefings/2026-10-17" in raw_str
    assert "台积电上调全年资本开支指引" in raw_str


def test_http_server_serves_briefing_and_archive(tmp_db):
    html_doc = render_market_map_html(
        "2026-10-17", "早餐标题", "https://wallstreetcn.com/articles/1", "2026-10-17 07:05", SAMPLE_STRUCTURED
    )
    briefing_repo.save_briefing(
        "2026-10-17", "1", "早餐标题", "https://wallstreetcn.com/articles/1",
        "2026-10-17 07:05", SAMPLE_STRUCTURED, html_doc
    )
    srv = start_briefing_http_server(host="127.0.0.1", port=0)
    port = srv.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/briefings/2026-10-17", timeout=5) as resp:
            body = resp.read().decode("utf-8")
            assert resp.status == 200 and "台积电上调全年资本开支指引" in body
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/briefings", timeout=5) as resp:
            arc = resp.read().decode("utf-8")
            assert resp.status == 200 and "2026-10-17 早餐" in arc
    finally:
        srv.shutdown()


def test_morning_briefing_job_retry_and_error_alert(monkeypatch, tmp_db):
    briefing_repo.set_subscription("oc_sub1", True, "p2p")
    briefing_repo.set_subscription("oc_sub2", True, "group")

    # 1) 08:00 源站尚未更新 (is_final_attempt=False) -> 不打扰用户, 返回暂未更新_待重试
    async def not_updated(*args, **kwargs):
        raise BriefingNotUpdatedError("2026-10-18", latest_date="2026-10-17")

    monkeypatch.setattr(briefing_service, "get_or_generate_briefing", not_updated)
    n = RecordingNotifier()
    s1 = run(jobs.morning_briefing_job(n, target_date="2026-10-18", is_final_attempt=False))
    assert s1["skipped"] == "暂未更新_待重试" and n.sent == []

    # 2) 08:30 最终检查仍未更新 (is_final_attempt=True) -> 发送状态提示卡片(含最近一期回顾链接)
    s2 = run(jobs.morning_briefing_job(n, target_date="2026-10-18", is_final_attempt=True))
    assert s2["status_alerted"] == 2 and len(n.sent) == 2
    assert n.sent[0]["title"] == "2026-10-18 早餐"
    assert "2026-10-17" in n.sent[0]["text"]

    # 3) 正常生成成功 -> 推送早报卡片并标记 pushed_at, 后续重试点自动跳过
    html_doc = render_market_map_html(
        "2026-10-18", "早餐标题", "https://wallstreetcn.com/articles/2", "2026-10-18 07:05", SAMPLE_STRUCTURED
    )
    rec = briefing_repo.save_briefing(
        "2026-10-18", "2", "早餐标题", "https://wallstreetcn.com/articles/2",
        "2026-10-18 07:05", SAMPLE_STRUCTURED, html_doc
    )

    async def ok_gen(*args, **kwargs):
        return rec

    monkeypatch.setattr(briefing_service, "get_or_generate_briefing", ok_gen)
    n_ok = RecordingNotifier()
    s3 = run(jobs.morning_briefing_job(n_ok, target_date="2026-10-18", is_final_attempt=False))
    assert s3["pushed"] == 2
    # 再次触发(08:15/08:30)因已标记 pushed_at 自动跳过
    s4 = run(jobs.morning_briefing_job(n_ok, target_date="2026-10-18", is_final_attempt=True))
    assert s4["skipped"] == "今日已推送" and len(n_ok.sent) == 2
