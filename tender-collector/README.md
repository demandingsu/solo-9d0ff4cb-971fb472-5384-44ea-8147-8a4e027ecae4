# 市政工程投标截止时间采集服务

面向招投标专员的公告采集 API：提交市政工程公告站的**列表页地址**，服务自动遍历
所有分页与详情页，将**更正公告按其引用的原公告编号归并**到同一项目，并**按发布
时间**（而非页面排列顺序）确定当前生效的投标截止时间，标记是否已过期。

## 核心规则

1. **分页遍历**：从提交的列表页出发，沿 `nav.pagination` 链接抓取全部列表页
   （去重、防环、限量），收集所有详情页链接后逐页抓取。
2. **更正归并**：更正公告通过"原公告编号"归并到对应原公告所在项目；引用悬空
   （原公告不存在/不是原公告）视为数据错误，本次采集整体失败。
3. **时间解释**：同站公告的时间字段可能混用两种写法 ——
   - 无时区的本地时间（如 `2026-11-03 11:20`）一律按**北京时间（UTC+8）**解释；
   - 带偏移的 ISO 时间（如 `2026-11-03T03:20:00+00:00`）按**自身偏移**解释。

   两种写法一律换算为**实际时刻**后再比较/排序，结果与服务器本地时区无关；
   公告原文时间文本在结果中原样保留，不做格式归一化。
4. **生效截止时间**：同一项目内按发布时间的**实际时刻**排序，取最新且声明了
   截止时间的公告；时刻相同时按公告编号确定次序，结果与页面顺序完全无关。
   若最新更正公告未给出截止时间（例如只改联系人），沿用上一份带截止时间的公告。
5. **过期判定**：`is_expired = 截止时间的实际时刻 <= 当前时刻`（当前时刻按
   UTC 获取，与服务器本地时区无关）。
6. **幂等采集**：公告以编号为唯一键 UPSERT，重复采集不产生重复记录。
7. **失败保留**：任一列表页/详情页读取失败（网络错误、非 2xx、字段缺失）、
   **时间字段无法解释**或归并校验失败时，本次采集不写入任何公告数据，
   **完整保留上次成功采集结果**；失败原因记入 `collection_runs` 表并通过
   HTTP 422 返回。

## 目录结构

```
tender-collector/
├── app/                    # FastAPI 应用
│   ├── main.py             # 路由：/api/collect、/api/projects、/api/collections
│   ├── service.py          # 采集编排 + 事务边界 + 过期判定
│   ├── scraper.py          # httpx + BeautifulSoup 抓取/解析
│   ├── merge.py            # 更正归并与生效截止时间判定（纯函数）
│   ├── times.py            # 时间解析：无时区按北京时间、带偏移按实际时刻
│   ├── database.py         # SQLite 连接、建表、UPSERT
│   ├── models.py           # Pydantic 模型
│   └── config.py           # 环境变量配置
├── demo_site/              # 生成的本地分页 HTML 示例公告站（静态文件）
├── scripts/generate_demo_site.py  # 示例站生成器（数据可复现）
├── tests/test_collection.py       # 端到端 + 归并单元测试
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 快速开始（Docker Compose）

```bash
# 1. 生成示例公告站静态文件（已随仓库提供，可重复生成）
python3 scripts/generate_demo_site.py

# 2. 启动：API 在 :8000，示例公告站在 :8001
docker compose up --build -d

# 3. 提交列表页地址触发采集
curl -X POST http://localhost:8000/api/collect \
  -H 'Content-Type: application/json' \
  -d '{"list_url": "http://demo-site/list_1.html"}'

# 4. 查询项目当前截止时间
curl http://localhost:8000/api/projects
```

容器内 API 通过 compose 网络访问示例站（主机名 `demo-site`）；从宿主机浏览
示例站则打开 <http://localhost:8001/list_1.html>。SQLite 数据存于命名卷
`tender-data`（容器内 `/data/tenders.db`）。

停止与清理：

```bash
docker compose down          # 停止（保留数据卷）
docker compose down -v       # 停止并删除采集数据
```

## 本地开发运行（不使用 Docker）

```bash
python3 -m pip install -r requirements-dev.txt
python3 scripts/generate_demo_site.py

# 终端 A：示例公告站
python3 -m http.server 8001 --directory demo_site

# 终端 B：API（数据库默认在 ./data/tenders.db）
uvicorn app.main:app --reload --port 8000

# 终端 C：测试
pytest -q

# 采集
curl -X POST http://localhost:8000/api/collect \
  -H 'Content-Type: application/json' \
  -d '{"list_url": "http://127.0.0.1:8001/list_1.html"}'
```

交互式 API 文档（Swagger UI）：<http://localhost:8000/docs>

## API 说明

### `POST /api/collect` — 提交列表页，触发采集

请求：

```json
{ "list_url": "http://demo-site/list_1.html" }
```

成功响应（200）：

```json
{
  "status": "success",
  "source_url": "http://demo-site/list_1.html",
  "collected": 12,
  "projects": [
    {
      "project_name": "城北污水处理厂扩建工程",
      "current_deadline": "2026-11-30T10:00:00+08:00",
      "deadline_source_code": "SZ-2026-002-G2",
      "deadline_source_url": "http://demo-site/SZ-2026-002-G2.html",
      "is_expired": false
    }
  ]
}
```

`current_deadline` 为公告原文时间文本（可能带时区偏移，原样保留）；
`deadline_source_url` 为生效公告的来源链接。

失败响应（422，任一页面不可读/时间字段无法解释/数据无法归并）：

```json
{ "detail": "读取页面失败 http://demo-site/SZ-2026-002-G2.html: HTTP 404" }
```

### `GET /api/projects` — 查询全部项目的当前截止时间

返回字段：项目名称、当前生效截止时间、截止时间来源公告编号、来源链接、是否过期。

### `GET /api/collections?limit=20` — 采集运行历史

包含每次采集的成功/失败状态、采集条数与错误信息。

## 示例站数据设计

`demo_site/` 含 6 个项目、12 条公告、3 个列表页，列表按发布时间实际时刻
**倒序**排列（更正公告排在原公告之前），专门用于验证归并不受页面顺序影响。
时间字段刻意**混用无时区北京时间与带偏移 ISO 时间**（`+08:00` / `+00:00`），
其中项目 B 的第一次更正"字符串排序靠后、实际时刻更早"，用于验证服务按
实际时刻而非字符串排序确定生效更正：

| 项目 | 场景 | 生效截止时间来源 |
| --- | --- | --- |
| 滨河路市政改造工程 | 一次更正延期（更正时间带 `+08:00` 偏移） | 更正公告 SZ-2026-001-G1 |
| 城北污水处理厂扩建工程 | 两次更正混用两种时间写法，取实际时刻最新的第二次 | 第二次更正 SZ-2026-002-G2 |
| 东风桥加固维修工程 | 无更正，已过期 | 原公告 SZ-2026-003 |
| 高新区雨污分流管网工程 | 更正（带偏移）不改截止时间 | 原公告 SZ-2026-004 |
| 湖滨公园配套市政道路工程 | 原公告在末页、更正在首页（跨页乱序） | 更正公告 SZ-2026-005-G1 |
| 江滩排涝泵站改造工程 | 更正使用 UTC（`+00:00`）偏移写法 | 更正公告 SZ-2026-006-G1 |

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TENDER_DB_PATH` | 本地 `./data/tenders.db`；容器内 `/data/tenders.db` | SQLite 文件路径 |
| `TENDER_HTTP_TIMEOUT` | `15` | 单个页面请求超时（秒） |
| `TENDER_MAX_LIST_PAGES` | `200` | 单次采集最多遍历的列表页数（防环） |
| `TENDER_USER_AGENT` | `TenderCollector/1.0 ...` | 请求 UA |

## 解析约定（接入真实站点时）

解析器依赖以下稳定选择器，适配其他站点时调整 `app/scraper.py` 即可：

- 列表页：`ul.announcement-list a.detail-link` 为详情链接，
  `nav.pagination a` 为分页链接；
- 详情页：`article.announcement-detail` 容器，支持
  `data-code` / `data-kind` / `data-published-at` / `data-corrects` 属性，
  以及 `table.detail-table` 内 `th[data-field="字段名"] + td` 表格字段
  （公告编号、公告类型、项目名称、发布时间、投标截止时间、原公告编号）。
