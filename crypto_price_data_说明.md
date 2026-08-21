# 加密货币日线研究数据集说明

本数据集用于加密货币多因子研究,包含 **Binance 现货 Top100(按 24h 成交额)** 的完整日线行情,仿照 A 股 `price_data.parquet` 的格式组织。两份交付物:

| 文件 | 位置 | 内容 |
|---|---|---|
| 原始分币文件 ×100 | `user_data/data/binance/` | 每币一份日线 feather |
| 聚合文件 | `./crypto_price_data.parquet` | 100 币合成长表(本说明主要对象) |

数据来源:Binance 现货 (`api.binance.com`),下载日期 2026-08-21,行情截至 2026-08-20(UTC)。

---

## 一、原始 100 份文件

### 1.1 位置与命名
- 目录:`user_data/data/binance/`
- 命名:`<币种>_USDT-1d.feather`,如 `BTC_USDT-1d.feather`、`ETH_USDT-1d.feather`
- 每份一个币,共 **100 份**,无缺失

### 1.2 格式(feather,Apache Arrow)
| 列 | 类型 | 说明 |
|---|---|---|
| `date` | datetime64[ms, UTC] | 交易日(UTC) |
| `open` / `high` / `low` / `close` | float64 | OHLC 价格(USDT) |
| `volume` | float64 | 基础资产成交量(如 BTC 数量,非 USDT 额) |

### 1.3 更新方式
```bash
freqtrade download-data -c user_data/config/config_spot_research.json \
  --timeframes 1d --trading-mode spot \
  --new-pairs-days 3650 --no-parallel-download --erase
```
- 必须走 SOCKS 代理(`config_spot_research.json` 已配好);直连 Binance 被地理封锁
- 必须 `--erase` 才能重拉全历史(本地已有数据时默认只续尾部)
- `--no-parallel-download` 逐对串行,避免并发打爆代理隧道

---

## 二、聚合文件 `crypto_price_data.parquet`

### 2.1 结构
长表,双索引 `(trade_date, code)`,**9 列**:

```
shape: (126,379, 9)
index: trade_date(datetime) + code(str, 如 "BTC/USDT")
```

| 列 | 类型 | 说明 |
|---|---|---|
| `is_st` | bool | 风险警示标记。加密无 ST 机制,统一为 `False` |
| `trade_days` | float32 | 上市后累计交易日数(第 1 天=1) |
| `open` | float64 | 开盘价(USDT) |
| `high` | float64 | 最高价 |
| `low` | float64 | 最低价 |
| `close` | float64 | 收盘价 |
| `vol` | float64 | 基础资产成交量(与原始文件 `volume` 相同) |
| `amount` | float64 | 成交额(USDT),**近似值** = `close × vol` |
| `pct_chg` | float64 | 日涨跌幅,单位 %,= `(close/前日close - 1) × 100` |

### 2.2 数据规模
- 币数:100 个;行数:126,379
- 每币行数:35 ~ 3,291(新上市币少,老币多)
- 覆盖区间:BTC/ETH 自 **2017-08-17** 上市日起,最新至 **2026-08-20**
- 各币历史跨度:中位约 2.5 年,最长约 9 年

### 2.3 加载示例
```python
import pandas as pd
df = pd.read_parquet("crypto_price_data.parquet")   # (trade_date, code) 双索引
# 单币切片
btc = df.xs("BTC/USDT", level="code")
# 某日全市场横截面(因子分析常用)
cross = df.xs("2026-08-01", level="trade_date")
# 全市场面板(行业/市值中性化以外的常规因子计算)
wide_close = df["close"].unstack("code")
ret = wide_close.pct_change()
```

---

## 三、与 `price_data.parquet`(A股)的格式对应

| A股字段 | 本文件 | 说明 |
|---|---|---|
| `trade_date` + `code` 索引 | 同左 | 长表双索引 |
| `is_st` | `is_st` | 加密无 ST,恒为 False |
| `trade_days` | `trade_days` | 口径一致(累计交易日) |
| `open/high/low/close` | 同左 | — |
| `vol` | `vol` | 基础量 |
| `amount` | `amount` | 成交额,近似 |
| `pct_chg` | `pct_chg` | 单位一致(%) |
| `open_adj~close_adj` | **已删除** | 加密 24/7 交易、无拆股分红,复权=原价,无意义 |
| `mkt_cap` | **已删除** | 不做市值中性化 |
| `industry` | **已删除** | 不做行业中性化 |
| — | — | 未加换手率(需市值,用户不需要) |

---

## 四、标的池口径与注意事项

### 4.1 池子构建
按 Binance 现货 **24h 成交额降序取 Top100**,过滤规则(见 `/tmp/build_spot_universe.py`):
- 仅 USDT 计价对
- 剔除:稳定币、黄金代币(XAUT/PAXG)、包装/质押衍生品(WBETH 等)、币安 Beta 美股代币(B 后缀)、杠杆代币(3L/3S/UP/DOWN 等)、非 ASCII 符号
- 剔除 `active=False` 的已下架/停牌对(如 TON/UTK/NFP/LRC 已下架,NIL/TST 停牌)

### 4.2 口径注意
- **价格均为现货未复权原始价**(加密无需复权)
- **`amount` 为近似**:原始 kline 只存基础量,成交额用 `close × vol` 近似,未含日内价格变动
- **`pct_chg` 首日(每币第 1 行)为 NaN**;相邻无缺失
- 时间戳为 UTC;与 A股(北京时间)需自行换算
- 若需复现池子或换池,重跑 `/tmp/build_spot_universe.py`(需代理)

### 4.3 生成脚本
`/tmp/build_crypto_parquet.py` — 从 100 份 feather 聚合生成 parquet,可重复执行。
