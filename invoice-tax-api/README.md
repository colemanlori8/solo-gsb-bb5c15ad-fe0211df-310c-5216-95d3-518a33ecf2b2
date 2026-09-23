# 发票税额计算 API（invoice-tax-api）

基于 **FastAPI + SQLite** 的发票税额计算服务：

- 全程 `Decimal` 精确计算，**禁用 float**，金额以字符串传输与存储
- 每行税额按**行币种最小货币单位** `ROUND_HALF_UP` 舍入
- 逐行舍入合计与整体舍入总额之间的**残差**，按**固定规则（最大余数法 Hamilton）**分摊，结果确定、可复算
- **跨币种**：行级携带汇率换算到发票币种，**原币金额完整保留**并分币种汇总
- SQLite 持久化，提供创建 / 查询 / 分页列表 / 复算 / 健康检查

---

## 一、启动

### Docker Compose（推荐）

```bash
cd invoice-tax-api
docker compose up -d --build
```

服务地址：**http://localhost:8000**

| 地址 | 说明 |
|---|---|
| `http://localhost:8000/health` | 健康检查 |
| `http://localhost:8000/docs` | Swagger 交互文档 |
| `http://localhost:8000/api/v1/invoices` | 发票 API |

修改对外端口：`HOST_PORT=9000 docker compose up -d`（默认 8000）。

### 本地运行（不用 Docker）

```bash
pip install -r requirements.txt
SQLITE_PATH=./invoices.db uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 运行测试

```bash
pip install pytest httpx
python -m pytest tests/ -q        # 17 个用例
```

---

## 二、配置（环境变量）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SQLITE_PATH` | 容器内 `/data/invoices.db` | SQLite 数据库文件路径，Compose 中由命名卷 `invoice-data` 持久化 |
| `CURRENCY_MIN_UNITS` | 内置表 | JSON 覆盖/扩展币种最小单位，如 `{"USD":"0.01","JPY":"1","XAU":"0.0001"}` |
| `HOST_PORT` | `8000` | 仅 Compose：宿主机映射端口 |

内置最小单位：CNY/USD/EUR/HKD/AUD/GBP/SGD = `0.01`，JPY/KRW = `1`。

---

## 三、API

### 1. 健康检查

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

### 2. 创建发票（计算并持久化）

`POST /api/v1/invoices`

```bash
curl -X POST http://localhost:8000/api/v1/invoices \
  -H 'Content-Type: application/json' \
  -d '{
    "invoice_no": "INV-2026-0001",
    "currency": "CNY",
    "lines": [
      {"description": "进口服务器", "currency": "USD", "net": "1299.50", "tax_rate": "0.10", "fx_rate": "7.1000"},
      {"description": "本地服务",   "currency": "CNY", "net": "500.00",  "tax_rate": "0.13"},
      {"description": "运费",       "currency": "CNY", "net": "0.05",    "tax_rate": "0.09"}
    ]
  }'
```

字段规则：

- `invoice_no`：发票号，**唯一**，重复返回 `409`
- `currency`：发票币种（残差分摊与总额的币种）
- 行内 `net` / `tax_rate` / `fx_rate`：**十进制字符串**（拒绝 float / 科学计数法 / NaN）
- `tax_rate` ∈ [0, 1]；行币种 = 发票币种时 `fx_rate` 必须为必须为 1（可省略）

响应（节选）：

```json
{
  "id": 1,
  "invoice_no": "INV-2026-0001",
  "check_ok": true,
  "result": {
    "invoice_currency": "CNY",
    "invoice_min_unit": "0.01",
    "mixed_currency": true,
    "total_tax_exact": "987.64950000",
    "total_tax": "987.65",
    "sum_line_rounded_tax": "987.65",
    "residual": "0.00",
    "residual_units": 0,
    "currency_totals": [
      {"currency": "CNY", "net_total": "500.05", "tax_total": "65.00"},
      {"currency": "USD", "net_total": "1299.50", "tax_total": "129.95"}
    ],
    "lines": [
      {
        "index": 0, "description": "进口服务器", "currency": "USD",
        "net": "1299.50", "tax_rate": "0.10",
        "tax_original_exact": "129.950", "tax_original_rounded": "129.95",
        "fx_rate_to_invoice": "7.1000",
        "tax_invoice_exact": "922.645000", "tax_invoice_rounded": "922.65",
        "allocation_adjust_units": 0, "tax_final": "922.65"
      }
    ]
  }
}
```

### 3. 查询

```bash
curl http://localhost:8000/api/v1/invoices/1            # 按自增 ID
curl http://localhost:8000/api/v1/invoices/by-no/INV-2026-0001   # 按发票号
```

### 4. 分页列表

```bash
curl "http://localhost:8000/api/v1/invoices?page=1&page_size=20&currency=CNY"
# {"page":1,"page_size":20,"total":3,"items":[...]}   按创建时间倒序
```

### 5. 复算校验

```bash
curl -X POST http://localhost:8000/api/v1/invoices/1/recompute
# {"identical": true, "stored_total_tax": "987.65", "recomputed_total_tax": "987.65", ...}
```

用持久化的原始请求重新完整计算并逐字段比对，验证结果可复算。

---

## 四、计算规则（固定、可复算）

1. 行净额按**行币种最小单位** `HALF_UP` 量化；
2. 行税额精确值 = 净额 × 税率（不提前舍入）；
3. 行税额按行币种单位 `HALF_UP` 舍入 → `tax_original_rounded`（原币保留）；
4. 跨币种：精确税额 × 行汇率换算到发票币种（**用未舍入值换算，避免二次舍入**），再按发票币种单位 `HALF_UP`；
5. 发票总税额 = Σ 换算后精确税额，整体 `HALF_UP`；
6. **残差** = 整体舍入总额 − Σ 逐行舍入额（必为最小单位整数倍），按**最大余数法**分摊：
   - 残差 > 0：按「精确税额相对向下取整的余数」**降序**，前 k 行各 **+1 最小单位**；
   - 残差 < 0：按余数**升序**，前 |k| 行各 **−1 最小单位**；
   - 并列：税额绝对值大者优先，再相同则行号靠前者优先；
7. 不变式：分摊后 **Σ 各行 `tax_final` == `total_tax`**，残差清零；创建时自动复算一次并落库 `check_ok`。

---

## 五、项目结构

```
invoice-tax-api/
├── app/
│   ├── main.py          # FastAPI 路由
│   ├── tax_engine.py    # Decimal 计算核心 + 残差分摊
│   ├── schemas.py       # Pydantic 请求/响应模型
│   ├── db.py            # SQLite 持久化
│   └── config.py        # 币种最小单位等常量
├── tests/               # 17 个 pytest 用例（引擎 + API）
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── README.md
```
