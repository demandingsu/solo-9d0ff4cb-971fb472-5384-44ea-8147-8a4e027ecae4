"""端到端测试：用线程内静态 HTTP 服务器托管生成的示例公告站，
通过 FastAPI TestClient 走完整的采集/查询/失败保留流程。

示例站时间字段刻意混用无时区北京时间与带偏移 ISO 时间，
用于验证“无时区按北京时间、带偏移按实际时刻”的比较规则。
"""
from __future__ import annotations

import functools
import http.server
import importlib.util
import sys
import threading
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = ROOT / "demo_site"

# 加载示例数据生成器（脚本名带连字符，用 importlib）
spec = importlib.util.spec_from_file_location(
    "generate_demo_site", ROOT / "scripts" / "generate_demo_site.py"
)
gen = importlib.util.module_from_spec(spec)
sys.modules["generate_demo_site"] = gen
spec.loader.exec_module(gen)


# ---------------------------------------------------------------------------
# 静态文件服务器（支持可选的故障注入 / 内容替换）
# ---------------------------------------------------------------------------
class FlakyHandler(http.server.SimpleHTTPRequestHandler):
    # 类属性：需要返回 404 的路径集合；需要替换内容的路径 -> 字节内容
    fail_paths: set[str] = set()
    override_paths: dict[str, bytes] = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DEMO_DIR), **kwargs)

    def do_GET(self):  # noqa: N802
        if self.path in self.fail_paths:
            self.send_error(404, "injected failure")
            return
        if self.path in self.override_paths:
            body = self.override_paths[self.path]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def log_message(self, *args):  # 静默
        pass


@pytest.fixture(scope="session")
def demo_server():
    handler = functools.partial(FlakyHandler)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    yield f"http://{host}:{port}", httpd, FlakyHandler
    httpd.shutdown()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # 每个测试使用独立临时数据库
    monkeypatch.setenv("TENDER_DB_PATH", str(tmp_path / "test.db"))
    from app import config, database
    importlib.reload(config)
    import app.main as main
    importlib.reload(database)
    importlib.reload(main)
    database.init_db()
    with TestClient(main.app) as c:
        yield c, main, database


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def collect(client, base):
    resp = client.post("/api/collect", json={"list_url": f"{base}/list_1.html"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def project_map(payload):
    return {p["project_name"]: p for p in payload["projects"]}


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------
def test_full_collection_and_deadline_resolution(client, demo_server):
    c, main, _ = client
    base, _, _ = demo_server
    payload = collect(c, base)
    assert payload["collected"] == 12

    projects = project_map(payload)
    assert set(projects) == {a.project_name for a in gen.ANNOUNCEMENTS}

    # A：带 +08:00 偏移的更正公告延期生效，结果保留公告原文时间
    a = projects["滨河路市政改造工程"]
    assert a["current_deadline"] == "2026-11-25T14:30:00+08:00"
    assert a["deadline_source_code"] == "SZ-2026-001-G1"
    assert a["deadline_source_url"].endswith("SZ-2026-001-G1.html")
    assert a["is_expired"] is False

    # B：G1（带偏移）字符串排序靠后但实际时刻更早，生效截止时间必须取
    # 实际最新的 G2（无时区北京时间）；G2 的带偏移截止时间不应导致 500
    b = projects["城北污水处理厂扩建工程"]
    assert b["current_deadline"] == "2026-11-30T10:00:00+08:00"
    assert b["deadline_source_code"] == "SZ-2026-002-G2"
    assert b["is_expired"] is False

    # C：无更正、已过期
    cproj = projects["东风桥加固维修工程"]
    assert cproj["current_deadline"] == "2026-10-30 17:00"
    assert cproj["deadline_source_code"] == "SZ-2026-003"
    assert cproj["is_expired"] is True

    # D：最新更正（带偏移）未给截止时间，沿用上一份带时间的公告（原公告）
    d = projects["高新区雨污分流管网工程"]
    assert d["current_deadline"] == "2026-11-28 09:30"
    assert d["deadline_source_code"] == "SZ-2026-004"

    # E：原公告在最后一页、更正公告在第一页（跨页乱序）
    e = projects["湖滨公园配套市政道路工程"]
    assert e["current_deadline"] == "2026-11-20T15:00:00+08:00"
    assert e["deadline_source_code"] == "SZ-2026-005-G1"

    # F：更正公告用 UTC 偏移写法（= 北京时间 10:00，实际晚于原公告 09:00）
    f = projects["江滩排涝泵站改造工程"]
    assert f["current_deadline"] == "2026-11-13T10:00:00+00:00"
    assert f["deadline_source_code"] == "SZ-2026-006-G1"
    assert f["is_expired"] is False


def test_order_independence():
    """打乱输入顺序，归并结果必须一致。"""
    from app.merge import merge_projects

    rows = [
        {
            "code": a.code,
            "title": a.title,
            "kind": a.kind,
            "project_name": a.project_name,
            "published_at": a.published_at,
            "deadline": a.deadline,
            "corrects_code": a.corrects_code,
            "detail_url": f"http://x/{a.code}.html",
        }
        for a in gen.ANNOUNCEMENTS
    ]
    baseline = merge_projects(rows)
    shuffled = list(reversed(rows))
    import random

    random.Random(42).shuffle(shuffled)
    assert merge_projects(shuffled) == baseline


def test_merge_compares_actual_instant_not_string():
    """字符串排序靠后的带偏移更正，实际时刻更早时不应成为生效公告。"""
    from app.merge import merge_projects

    rows = [
        {
            "code": "P-1", "title": "t", "kind": "original",
            "project_name": "P", "published_at": "2026-11-03 11:20",
            "deadline": "2026-11-20 10:00", "corrects_code": None,
            "detail_url": "http://x/P-1.html",
        },
        {
            # 字符串上 "2026-11-03T10:30...+08:00" 排在 "2026-11-03 11:20" 之后，
            # 但实际时刻（北京时间 10:30）更早
            "code": "P-1-G1", "title": "t", "kind": "correction",
            "project_name": "P", "published_at": "2026-11-03T10:30:00+08:00",
            "deadline": "2026-11-25 10:00", "corrects_code": "P-1",
            "detail_url": "http://x/P-1-G1.html",
        },
    ]
    (result,) = merge_projects(rows)
    assert result.source_code == "P-1"
    assert result.deadline == "2026-11-20 10:00"


def test_merge_rejects_unparseable_time():
    """发布时间或截止时间无法解释时，归并直接报错（采集整体失败）。"""
    from app.merge import MergeError, merge_projects

    base_row = {
        "code": "P-1", "title": "t", "kind": "original",
        "project_name": "P", "published_at": "2026-11-03 11:20",
        "deadline": "2026-11-20 10:00", "corrects_code": None,
        "detail_url": "http://x/P-1.html",
    }
    bad_published = {**base_row, "published_at": "2026年11月3日 上午"}
    with pytest.raises(MergeError, match="P-1"):
        merge_projects([bad_published])

    bad_deadline = {**base_row, "deadline": "另行通知"}
    with pytest.raises(MergeError, match="P-1"):
        merge_projects([bad_deadline])


def test_is_expired_compares_actual_instant():
    """带偏移截止时间与（无时区的）当前时间都按实际时刻比较。"""
    from app.service import _is_expired

    now = datetime(2026, 11, 12, 16, 0)  # 无时区 -> 北京时间 16:00 (= 08:00 UTC)
    # 截止 09:00 UTC = 北京时间 17:00，未过期
    assert _is_expired("2026-11-12T09:00:00+00:00", now) is False
    # 截止 08:00 UTC = 北京时间 16:00，恰好到期
    assert _is_expired("2026-11-12T08:00:00+00:00", now) is True
    # 无时区截止按北京时间解释
    assert _is_expired("2026-11-12 15:59", now) is True
    assert _is_expired("2026-11-12 16:01", now) is False


def test_duplicate_collection_creates_no_duplicates(client, demo_server):
    c, main, database = client
    base, _, _ = demo_server
    collect(c, base)
    collect(c, base)  # 重复采集

    with database.get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
        distinct = conn.execute(
            "SELECT COUNT(DISTINCT code) FROM announcements"
        ).fetchone()[0]
        runs = conn.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0]
    assert total == 12
    assert distinct == 12
    assert runs == 2

    # 查询接口仍然是 6 个项目
    resp = c.get("/api/projects")
    assert resp.status_code == 200
    assert len(resp.json()) == 6


def test_detail_failure_keeps_last_successful_result(client, demo_server):
    c, main, database = client
    base, httpd, handler = demo_server

    # 首次成功采集
    first = collect(c, base)
    assert first["collected"] == 12

    # 注入故障：某个详情页 404
    handler.fail_paths = {"/SZ-2026-002-G2.html"}
    try:
        resp = c.post(
            "/api/collect", json={"list_url": f"{base}/list_1.html"}
        )
        assert resp.status_code == 422
        assert "SZ-2026-002-G2" in resp.json()["detail"] or "HTTP 404" in resp.json()["detail"]
    finally:
        handler.fail_paths = set()

    # 上次成功采集的完整结果必须保留
    resp = c.get("/api/projects")
    projects = {p["project_name"]: p for p in resp.json()}
    assert len(projects) == 6
    assert (
        projects["城北污水处理厂扩建工程"]["deadline_source_code"]
        == "SZ-2026-002-G2"
    )

    # 失败被记录，且不影响后续恢复采集
    runs = c.get("/api/collections").json()
    statuses = [r["status"] for r in runs]
    assert "failed" in statuses and "success" in statuses

    again = collect(c, base)
    assert again["collected"] == 12


def test_unparseable_time_keeps_last_successful_result(client, demo_server):
    """时间字段无法解释 -> 采集报错，且不写入任何部分新公告。"""
    c, main, database = client
    base, httpd, handler = demo_server

    first = collect(c, base)
    assert first["collected"] == 12

    # 把某更正公告的发布时间换成无法解释的文本（其余字段保持有效）
    bad = gen.Announcement(
        code="SZ-2026-001-G1",
        title="滨河路市政改造工程施工招标更正公告（第一次）",
        kind="correction",
        project_name="滨河路市政改造工程",
        published_at="2026年10月28日 16时",
        deadline="2026-11-25T14:30:00+08:00",
        corrects_code="SZ-2026-001",
        reason="因招标文件图纸调整，投标截止时间顺延。",
    )
    handler.override_paths = {
        "/SZ-2026-001-G1.html": gen._detail_page(bad).encode("utf-8")
    }
    try:
        resp = c.post(
            "/api/collect", json={"list_url": f"{base}/list_1.html"}
        )
        assert resp.status_code == 422
        assert "SZ-2026-001-G1" in resp.json()["detail"]
    finally:
        handler.override_paths = {}

    # 数据库完整保留上次成功结果，没有任何部分写入
    with database.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0] == 12
        bad_rows = conn.execute(
            "SELECT COUNT(*) FROM announcements WHERE published_at LIKE '%年%'"
        ).fetchone()[0]
        assert bad_rows == 0

    projects = {p["project_name"]: p for p in c.get("/api/projects").json()}
    assert len(projects) == 6
    assert (
        projects["滨河路市政改造工程"]["current_deadline"]
        == "2026-11-25T14:30:00+08:00"
    )

    # 失败运行已记录；故障解除后可恢复采集
    runs = c.get("/api/collections").json()
    assert runs[0]["status"] == "failed"
    assert collect(c, base)["collected"] == 12


def test_list_page_failure_returns_error(client, demo_server):
    c, main, database = client
    base, httpd, handler = demo_server
    collect(c, base)

    handler.fail_paths = {"/list_2.html"}
    try:
        resp = c.post(
            "/api/collect", json={"list_url": f"{base}/list_1.html"}
        )
        assert resp.status_code == 422
        assert "list_2" in resp.json()["detail"]
    finally:
        handler.fail_paths = set()

    # 数据仍是上次成功的 12 条
    with database.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0] == 12


def test_correction_referencing_missing_original_rejected():
    from app.merge import MergeError, merge_projects

    rows = [
        {
            "code": "X-1", "title": "t", "kind": "correction",
            "project_name": "P", "published_at": "2026-11-01 10:00",
            "deadline": "2026-12-01 10:00", "corrects_code": "X-0",
            "detail_url": "http://x/X-1.html",
        }
    ]
    with pytest.raises(MergeError, match="X-0"):
        merge_projects(rows)


def test_expired_flag_uses_fixed_now(client, demo_server):
    """注入固定当前时间验证过期判定边界（无时区当前时间按北京时间解释）。"""
    from app import service

    c, main, _ = client
    base, _, _ = demo_server
    collect(c, base)

    fixed_now = datetime(2026, 11, 26, 0, 0)
    projects = {p["project_name"]: p for p in service.query_projects(fixed_now)}
    # A 的截止 11-25 已过期；B 的 11-30 未过期
    assert projects["滨河路市政改造工程"]["is_expired"] is True
    assert projects["城北污水处理厂扩建工程"]["is_expired"] is False
    # F 的截止 2026-11-13T10:00:00+00:00（北京时间 11-13 18:00），已过期
    assert projects["江滩排涝泵站改造工程"]["is_expired"] is True

    # 采集当天（2026-11-12 中午，北京时间）：F 尚未过期
    same_day = datetime(2026, 11, 12, 12, 0)
    projects = {p["project_name"]: p for p in service.query_projects(same_day)}
    assert projects["江滩排涝泵站改造工程"]["is_expired"] is False
