#!/usr/bin/env python3
"""生成一个本地分页 HTML 示例公告站（市政工程招投标公告）。

生成结果写入 demo_site/，纯静态文件，可用任意 HTTP 服务器托管：
    python3 -m http.server 8001 --directory demo_site

数据特意构造为：
- 列表页按发布时间实际时刻倒序排列（更正公告排在其引用的原公告之前），
  服务端不能依赖页面顺序来判断截止时间；
- 时间字段混用无时区的北京时间（"2026-11-03 11:20"）与带偏移的 ISO 时间
  （"2026-11-03T03:20:00+00:00"），甚至出现“字符串排序靠后、实际时刻更早”
  的更正公告，服务端必须按实际时刻比较；
- 每个项目的“生效截止时间”由发布时间最新的那份公告（原公告或更正公告）决定；
- 含已过期与未过期两种项目。
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

PAGE_SIZE = 4
OUT_DIR = Path(__file__).resolve().parent.parent / "demo_site"

_BEIJING = timezone(timedelta(hours=8))


def _instant(text: str) -> datetime:
    """列表排序用：无时区值按北京时间，带偏移值按实际时刻。"""
    t = text.strip()
    if t.endswith(("Z", "z")):
        t = t[:-1] + "+00:00"
    dt = datetime.fromisoformat(t)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_BEIJING)
    return dt


@dataclass
class Announcement:
    code: str                      # 公告编号
    title: str                     # 公告标题
    kind: str                      # original | correction
    project_name: str              # 项目名称
    published_at: str              # 发布时间（无时区=北京时间，或带偏移 ISO）
    deadline: str | None = None    # 投标截止时间（更正公告可为空；格式同上混用）
    corrects_code: str | None = None  # 更正公告引用的原公告编号
    reason: str = ""               # 更正原因（仅更正公告）

    @property
    def filename(self) -> str:
        return f"{self.code}.html"


# ---------------------------------------------------------------------------
# 示例数据：6 个项目，其中 4 个含更正公告，共 12 条公告。
# 时间字段刻意混用无时区北京时间与带偏移 ISO 时间（+08:00 / +00:00）。
# ---------------------------------------------------------------------------
ANNOUNCEMENTS: list[Announcement] = [
    # 项目 A：原公告已过期，更正（带 +08:00 偏移）延期后仍在有效期内
    Announcement(
        code="SZ-2026-001",
        title="滨河路市政改造工程施工招标公告",
        kind="original",
        project_name="滨河路市政改造工程",
        published_at="2026-10-20 09:00",
        deadline="2026-11-05 14:30",
    ),
    Announcement(
        code="SZ-2026-001-G1",
        title="滨河路市政改造工程施工招标更正公告（第一次）",
        kind="correction",
        project_name="滨河路市政改造工程",
        published_at="2026-10-28T16:00:00+08:00",
        deadline="2026-11-25T14:30:00+08:00",
        corrects_code="SZ-2026-001",
        reason="因招标文件图纸调整，投标截止时间顺延。",
    ),
    # 项目 B：两次更正。G1 用带偏移写法、字符串排序靠后但实际时刻更早；
    # 生效截止时间必须取实际最新的 G2（无时区北京时间），且 G2 截止时间带偏移
    Announcement(
        code="SZ-2026-002",
        title="城北污水处理厂扩建工程招标公告",
        kind="original",
        project_name="城北污水处理厂扩建工程",
        published_at="2026-10-12 10:30",
        deadline="2026-11-10 09:00",
    ),
    Announcement(
        code="SZ-2026-002-G1",
        title="城北污水处理厂扩建工程招标更正公告",
        kind="correction",
        project_name="城北污水处理厂扩建工程",
        published_at="2026-11-03T10:30:00+08:00",  # 实际 10:30，早于 G2 的 11:20
        deadline="2026-11-18 09:00",
        corrects_code="SZ-2026-002",
        reason="答疑澄清，截止时间调整。",
    ),
    Announcement(
        code="SZ-2026-002-G2",
        title="城北污水处理厂扩建工程招标更正公告（第二次）",
        kind="correction",
        project_name="城北污水处理厂扩建工程",
        published_at="2026-11-03 11:20",  # 无时区北京时间，实际最晚
        deadline="2026-11-30T10:00:00+08:00",
        corrects_code="SZ-2026-002",
        reason="再次延期，以本公告时间为准。",
    ),
    # 项目 C：无更正，且已过期
    Announcement(
        code="SZ-2026-003",
        title="东风桥加固维修工程招标公告",
        kind="original",
        project_name="东风桥加固维修工程",
        published_at="2026-09-15 08:45",
        deadline="2026-10-30 17:00",
    ),
    # 项目 D：更正公告（带偏移）不修改截止时间，生效时间仍取原公告
    Announcement(
        code="SZ-2026-004",
        title="高新区雨污分流管网工程招标公告",
        kind="original",
        project_name="高新区雨污分流管网工程",
        published_at="2026-10-25 14:00",
        deadline="2026-11-28 09:30",
    ),
    Announcement(
        code="SZ-2026-004-G1",
        title="高新区雨污分流管网工程招标更正公告",
        kind="correction",
        project_name="高新区雨污分流管网工程",
        published_at="2026-11-01T09:10:00+08:00",
        deadline=None,
        corrects_code="SZ-2026-004",
        reason="仅更正联系人电话，投标截止时间不变。",
    ),
    # 项目 E：原公告在最后一个列表页、更正公告在第一页（跨页乱序）；
    # 同一条公告内发布时间与截止时间也混用两种格式
    Announcement(
        code="SZ-2026-005",
        title="湖滨公园配套市政道路工程招标公告",
        kind="original",
        project_name="湖滨公园配套市政道路工程",
        published_at="2026-10-08T13:00:00+08:00",
        deadline="2026-11-08 15:00",
    ),
    Announcement(
        code="SZ-2026-005-G1",
        title="湖滨公园配套市政道路工程招标更正公告",
        kind="correction",
        project_name="湖滨公园配套市政道路工程",
        published_at="2026-11-05 17:30",
        deadline="2026-11-20T15:00:00+08:00",
        corrects_code="SZ-2026-005",
        reason="工程量清单调整，截止时间顺延。",
    ),
    # 项目 F：更正公告使用 UTC（+00:00）偏移写法 —— 2026-10-18T02:00:00+00:00
    # 即北京时间 10:00，实际晚于原公告的北京时间 09:00，生效截止时间取更正公告
    Announcement(
        code="SZ-2026-006",
        title="江滩排涝泵站改造工程招标公告",
        kind="original",
        project_name="江滩排涝泵站改造工程",
        published_at="2026-10-18 09:00",
        deadline="2026-11-16 18:00",
    ),
    Announcement(
        code="SZ-2026-006-G1",
        title="江滩排涝泵站改造工程招标更正公告",
        kind="correction",
        project_name="江滩排涝泵站改造工程",
        published_at="2026-10-18T02:00:00+00:00",  # = 北京时间 2026-10-18 10:00
        deadline="2026-11-13T10:00:00+00:00",       # = 北京时间 2026-11-13 18:00
        corrects_code="SZ-2026-006",
        reason="开标场地调整，投标截止时间相应提前。",
    ),
]


def _detail_page(a: Announcement) -> str:
    rows = [
        ("公告编号", a.code),
        ("公告类型", "更正公告" if a.kind == "correction" else "招标公告"),
        ("项目名称", a.project_name),
        ("发布时间", a.published_at),
    ]
    if a.deadline is not None:
        rows.append(("投标截止时间", a.deadline))
    if a.kind == "correction":
        rows.append(("原公告编号", a.corrects_code or ""))
        rows.append(("更正事项", a.reason))

    trs = "\n".join(
        f'      <tr><th data-field="{html.escape(label)}">{html.escape(label)}</th>'
        f'<td data-value="{html.escape(label)}">{html.escape(value)}</td></tr>'
        for label, value in rows
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{html.escape(a.title)}</title>
</head>
<body>
  <article class="announcement-detail" data-code="{html.escape(a.code)}"
           data-kind="{a.kind}"
           data-published-at="{html.escape(a.published_at)}"
           {f'data-corrects="{html.escape(a.corrects_code)}"' if a.corrects_code else ""}>
    <h1 class="detail-title">{html.escape(a.title)}</h1>
    <table class="detail-table">
{trs}
    </table>
    <p><a href="list_1.html">返回列表</a></p>
  </article>
</body>
</html>
"""


def _list_page(items: list[Announcement], page: int, total_pages: int) -> str:
    lis = []
    for a in items:
        lis.append(
            f'      <li class="announcement-item" data-code="{html.escape(a.code)}">\n'
            f'        <a class="detail-link" href="{html.escape(a.filename)}">{html.escape(a.title)}</a>\n'
            f'        <span class="published-at">{html.escape(a.published_at)}</span>\n'
            f'      </li>'
        )
    nav_parts = []
    for p in range(1, total_pages + 1):
        if p == page:
            nav_parts.append(f'<span class="page-current">{p}</span>')
        else:
            nav_parts.append(f'<a class="page-link" href="list_{p}.html">{p}</a>')
    next_link = (
        f'<a class="page-next" href="list_{page + 1}.html">下一页</a>'
        if page < total_pages
        else '<span class="page-next-disabled">下一页</span>'
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>市政工程公告站 - 第 {page} 页</title>
</head>
<body>
  <h1>市政工程招投标公告列表（第 {page}/{total_pages} 页）</h1>
  <ul class="announcement-list" data-page="{page}">
{chr(10).join(lis)}
  </ul>
  <nav class="pagination">
    {''.join(nav_parts)}
    {next_link}
  </nav>
</body>
</html>
"""


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # 列表按发布时间实际时刻倒序 —— 更正公告天然排在原公告前面，制造页面顺序干扰
    ordered = sorted(ANNOUNCEMENTS, key=lambda a: _instant(a.published_at), reverse=True)
    total_pages = (len(ordered) + PAGE_SIZE - 1) // PAGE_SIZE

    for a in ordered:
        (OUT_DIR / a.filename).write_text(_detail_page(a), encoding="utf-8")

    for page in range(1, total_pages + 1):
        chunk = ordered[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
        (OUT_DIR / f"list_{page}.html").write_text(
            _list_page(chunk, page, total_pages), encoding="utf-8"
        )

    (OUT_DIR / "index.html").write_text(
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta http-equiv="refresh" content="0; url=list_1.html">'
        "<title>公告站</title></head><body>跳转中…</body></html>\n",
        encoding="utf-8",
    )
    print(f"generated {len(ordered)} detail pages, {total_pages} list pages in {OUT_DIR}")


if __name__ == "__main__":
    main()
