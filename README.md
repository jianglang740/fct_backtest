# 单因子回测框架 (Single Factor Backtest Framework)

一个面向 A 股市场的单因子量化回测框架，支持因子构建、预处理、分层回测、绩效归因和可视化。

## 项目结构

```
fct_backtest/
├── fct_backtest注释版.py          # 主回测脚本（详细注释版，推荐阅读）
├── fct_backtest无冗余版.py         # 主回测脚本（精简版，适合快速运行）
├── fct_backtest改进版.py           # 主回测脚本（改进版：每日截面预处理/
│                                  #   日历化调仓/FM回归+Newey-West/市值行业中性化）
├── 构造市值行业数据.py             # 基于米筐 rqdatac 拉取市值+行业，写入 price_data.parquet
├── 构造动量因子.py                 # 经典动量因子构建脚本（20/60/120日）
├── ic.py                          # IC 时间序列可视化脚本
├── price_data.parquet             # 行情数据（含 mkt_cap 市值 / industry 行业，Git LFS 托管）
├── fct_df.parquet                 # 因子数据（Git LFS 托管）
├── price_data_backup.parquet      # 拉取市值/行业前的原始数据备份（验证后可删除）
├── 图片/                          # 回测输出图表
│   ├── Figure_1~5.png             # 分层收益曲线 + 绩效仪表盘
│   └── IC时序图.png               # IC 时间序列图
├── 说明文档/
│   ├── 改进版框架说明.md           # 改进版四大改进、新指标、用法详解
│   ├── 数据说明报告.md             # 两份数据的字段、统计特征详解
│   ├── 数据透视与回测原理说明.md    # 长表⇄宽表转换与回测原理拆解
│   ├── 回测流程图.md               # 原版 + 改进版 Mermaid 流程图
│   ├── parquet文件格式.md          # Parquet vs CSV 对比及行业最佳实践
│   └── 该框架的局限性.md           # 框架优缺点分析，实盘适用性评估
└── README.md
```

## 功能特性

**原版（注释版 / 无冗余版）**

- **因子构建**：内置经典动量因子生成脚本，支持多窗口（20/60/120 日）
- **稳健的数据预处理**：MAD 缩尾去极值（中位数 ± 5.2×MAD）+ Z-Score 标准化
- **严格的样本池过滤**：自动剔除 ST 股票和上市不足一年的次新股
- **多持有期分层回测**：支持 1/5/10/20 天持有期，每个交易日按因子值等分为 10 组
- **完整的绩效指标体系**：IC、ICIR、分层累计收益、多空收益、夏普/索提诺/卡玛比率
- **无样本重叠**：通过 `iloc[::period]` 隔行采样，保证收益序列独立同分布
- **可视化输出**：分层累计收益曲线 + 因子绩效仪表盘（2×2 子图）
- **Excel 结果导出**：汇总统计 + 分位数收益 + IC 时间序列，三个 Sheet

**改进版（`fct_backtest改进版.py`）在保留原版全部能力的基础上修复四大问题**

- **每日截面预处理**：MAD / Z-score 统计量只用当日股票，消除全局前视偏差
- **日历化调仓**：支持 auto / daily / weekly / monthly（月末调仓适配月频/财报因子），默认持有期按调仓方式自适应（auto/daily 为 1/5/10/20 天，weekly/monthly 默认 1 个窗口）
- **重叠窗口策略收益重建**：日历化调仓 + 多持有期（period>1）按"全持仓·等权·重叠加仓"重建真实策略收益，不再对重叠窗口重复复利（修复 daily 20 天多空收益曾虚增至 −844 万% 等假数字），NW 滞后阶数覆盖重叠长度；无重叠场景口径逐点不变
- **Fama-MacBeth 回归**：逐日截面回归求风险溢价斜率（BP），并做 **Newey-West 自相关修正**（IC / 多空 / 斜率三个 t 值）
- **市值 + 行业中性化**：Barra 风格残差化剥离市值与行业，输出原始 vs 中性化对比及市值相关诊断
- **Excel 结果导出**：五个 Sheet（新增 `FM回归与NW检验`、`中性化诊断`）
- **丰富可视化**：6 类图（分层收益叠加中性化虚线、绩效仪表盘、IC 分布、FM 回归、t 值 naive vs NW 对比、中性化对比仪表盘），所有图表**自动保存** PNG 到 `code/figs/`，`FCT_SHOW_FIGS=1` 时额外弹窗显示
- **参数可环境变量覆盖**：`FCT_FACTOR` / `FCT_REBALANCE` / `FCT_PERIODS` 等，便于批量测试

## 环境依赖

- Python ≥ 3.8
- pandas
- numpy
- matplotlib
- scipy
- pyarrow（读取 Parquet 文件）
- openpyxl（写入 Excel 文件）

### 安装依赖

```bash
pip install pandas numpy matplotlib scipy pyarrow openpyxl
```

## 快速开始

### 1. 克隆仓库（含数据文件）

本项目使用 Git LFS 管理大型数据文件，请确保已安装 Git LFS：

```bash
# 安装 Git LFS（如未安装）
brew install git-lfs        # macOS
# 或 apt install git-lfs   # Linux

# 克隆仓库
git clone https://github.com/jianglang740/fct_backtest.git
cd fct_backtest
git lfs pull
```

### 2. 运行回测

```bash
# 注释版（推荐，适合学习理解）
python fct_backtest注释版.py

# 精简版（适合快速运行）
python fct_backtest无冗余版.py

# 改进版（每日截面预处理 / 日历化调仓 / FM回归+Newey-West / 市值行业中性化）
python fct_backtest改进版.py
```

运行后将输出：

- 控制台打印预处理后的数据概览和汇总统计表
- 所有图表保存到 `code/figs/`：分层收益曲线（每持有期一张）+ 绩效仪表盘 + IC 分布 + FM 回归 + t 值对比（+ 中性化对比仪表盘）
- 生成 Excel 统计结果：注释版 → `单因子回测统计结果.xlsx`（3 Sheet）；无冗余版 / 改进版 → `因子回测统计结果.xlsx`（改进版 5 Sheet）

> 首次使用改进版前，先用米筐环境拉取市值/行业数据（仅需一次）：
> `/opt/anaconda3/envs/Ricequant_SDK/bin/python 构造市值行业数据.py`

### 3. 调整改进版参数（环境变量）

```bash
# 例：回测 fct_1，月末调仓，5 组，市值+行业中性化
# （月频默认持有期=(1,)，即"持有 1 个月"；图表自动保存到 code/figs/，只有 1 张分层曲线）
FCT_FACTOR=fct_1 FCT_REBALANCE=monthly \
FCT_QUANTILES=5 FCT_NEUTRALIZE=mktcap_industry \
python fct_backtest改进版.py

# 如需月频多窗口（持有 1/3/6/12 个月），显式指定 FCT_PERIODS：
FCT_REBALANCE=monthly FCT_PERIODS=1,3,6,12 python fct_backtest改进版.py
```

参数详见 [改进版框架说明](说明文档/改进版框架说明.md)。

### 4. 构建自定义因子

使用 `构造动量因子.py` 生成动量因子，或参考其逻辑构建你自己的因子：

```bash
python 构造动量因子.py
```

该脚本基于 `price_data.parquet` 中的后复权收盘价，生成 20 日、60 日、120 日三个窗口的动量因子，输出到 `fct_df.parquet`。

### 5. 切换测试因子

修改主回测脚本底部 `main` 中的因子选择：

```python
# 测试 mom_20d
factor_df = price_data['mom_20d']

# 或测试 mom_60d / mom_120d
factor_df = price_data['mom_60d']
```

### 6. 调整持有期

修改 `factor_analysis` 调用的 `periods` 参数：

```python
results = factor_analysis(factor_df, price_data, periods=(1, 5, 10, 20), quantiles=10)
```

### 7. 生成 IC 时序图

```bash
python ic.py
```

从回测输出的 Excel 中读取 IC 时间序列，绘制带统计标注的专业 IC 走势图。

## 回测流程

**原版**

```
数据加载 ──→ 数据合并 ──→ 因子预处理 ──→ 样本池过滤
                       (MAD缩尾       (去ST+去次新股)
                       + Z-Score)

    ↓

分层回测 ──→ 绩效分析 ──→ 方向判断 ──→ 组合指标 ──→ 结果输出
(长→宽→shift  (IC/ICIR/   (正向/负向)   (夏普/索提诺/   (Excel+图表)
 算收益率→    分层收益/                 卡玛/最大回撤)
 分10组)      多空收益)
```

**改进版**（在分层回测的基础上新增三处：截面预处理、日历化调仓、统计检验与中性化）

```
数据加载(含市值/行业) ──→ 样本池过滤 ──→ 每日截面 MAD+Z-score ──→ 日历化调仓
                                            (去ST+次新)          (auto/weekly/monthly/daily)
                                                                     ↓
                                       Fama-MacBeth 回归 + Newey-West t 检验
                                       ↑          市值+行业中性化（逐日截面残差化）
                                       └──── 分层收益 / IC / 多空收益（含显著性）
                                                                     ↓
                                     结果输出（Excel 5 Sheet + 图表）
```

完整的 Mermaid 流程图见 [回测流程图](说明文档/回测流程图.md)。

## 核心方法论

### 因子预处理

| 步骤   | 方法     | 说明                                             |
| ------ | -------- | ------------------------------------------------ |
| 去极值 | MAD 缩尾 | `clip(median ± 5.2×MAD)`，比均值±3σ 更稳健 |
| 标准化 | Z-Score  | `(x − μ) / σ`，使因子跨截面可比             |
| 缺失值 | 填 0     | 标准化后填 0 即填充到均值水平                    |

> 原版对全量数据一次性做 MAD/Z-score（全局统计量）；改进版先过滤样本池，再**按每日截面** `groupby(level='trade_date')` 逐日计算，消除前视偏差。

### 分层回测

每个交易日按因子值将股票等分为 10 组（Q1~Q10），计算各组未来 1/5/10/20 天的平均收益率。为避免样本重叠，`iloc[::period]` 每隔 period 行取一个截面（改进版改为**日历化调仓**：auto / daily / weekly / monthly，月末调仓可适配月频/财报因子）。

### 绩效指标

| 指标                 | 计算方式                           | 含义                           |
| -------------------- | ---------------------------------- | ------------------------------ |
| **IC**         | `mean(Spearman(因子, 未来收益))` | 因子预测能力，\|IC\| 越大越好  |
| **ICIR**       | `IC_mean / IC_std`               | IC 稳定性，越大越可靠          |
| **多空收益**   | Q10 累计收益 − Q1 累计收益        | 最高组做多、最低组做空的收益差 |
| **FM斜率(BP)** | 每日截面 OLS 斜率均值 × 10000      | 因子每变动 1 单位带来的收益（基点），改进版新增 |
| **IC_t_NW**    | IC 均值的 Newey-West t 值          | 修正自相关后的 IC 显著性，改进版新增 |
| **FM_t_NW**    | FM 斜率的 Newey-West t 值          | 风险溢价是否显著非零，改进版新增 |
| **夏普比率**   | `年化收益 / 年化波动率`          | 单位总风险的超额收益           |
| **索提诺比率** | `年化收益 / 下行波动率`          | 只惩罚下行风险                 |
| **卡玛比率**   | `年化收益 / 最大回撤`            | 收益与回撤的性价比             |
| **最大回撤**   | `min(净值 / 历史最高 − 1)`      | 最大净值回落幅度               |

## 代码结构

```
fct_backtest注释版.py / fct_backtest无冗余版.py
├── extreme_MAD()                # MAD 缩尾去极值
├── standardize_z()              # Z-Score 标准化
├── preprocess_data()            # 样本池过滤
├── factor_analysis()            # 核心：分层回测（长→宽→shift→收益→分10组）
├── analyze_factor_performance() # IC/ICIR/分位收益/多空收益
├── determine_factor_direction() # 判断正向/负向因子
├── calculate_portfolio_metrics()# 夏普/索提诺/卡玛/最大回撤
├── generate_summary_statistics()# 输出 Excel 三 Sheet
├── plot_quantile_returns_separate()# 分层累计收益曲线
├── plot_factor_performance()    # 绩效仪表盘（2×2）
└── main                         # 数据加载 → 预处理 → 回测 → 输出

fct_backtest改进版.py（在注释版基础上重构）
├── extreme_MAD() / standardize_z()      # 每日截面版（配合 groupby transform 使用）
├── preprocess_data()                    # 样本池过滤（先过滤，后算截面统计量）
├── preprocess_factors()                 # 逐日截面 MAD + Z-score + fillna(0)
├── build_rebalance_dates()              # 日历化调仓：auto/daily/weekly/monthly
├── rebalance_period_days()              # 各调仓方式折算年化口径（21/5/period）
├── factor_analysis()                    # 窗口锚定收益 + 截面分层（携带市值/行业）
├── newey_west_se()                      # Newey-West HAC 修正标准误（Bartlett 权重）
├── fama_macbeth_regression()            # 逐日截面 OLS → 平均斜率(BP) + naive/NW t
├── neutralize_factor()                  # 市值+行业残差化中性化（Barra 风格）
├── _factor_metrics()                    # 单因子全套指标（分层/IC/FM/NW t）
├── analyze_factor_performance()         # 原始 vs 中性化指标 + 市值相关诊断
├── effective_metrics() / determine_factor_direction()
├── generate_summary_statistics()        # 输出 Excel 五 Sheet
├── plot_quantile_returns_separate() / plot_factor_performance()
└── main                                 # 支持 FCT_* 环境变量覆盖参数

构造市值行业数据.py（数据拉取，米筐环境）
├── to_rq_code() / to_local_code()       # 本地后缀 ⇄ 米筐后缀（.SZ/.XSHE）
├── pull_market_cap()                    # rqdatac market_cap 因子，分块拉取
├── pull_industry_at()                   # 申万一级行业（单日期）
├── build_industry_series()              # 月度锚点 + 前向填充
└── main                                 # 备份 → 拉取 → 写回 → 覆盖率诊断
```

## 扩展指南

### 添加新因子

在 `fct_df.parquet` 中新增一列（如 `mom_180d`），或修改 `构造动量因子.py` 中的 `momentum_windows` 字典添加新窗口。主回测脚本中的 `factor_names = fct_df.columns` 会自动识别所有因子列。

### 自定义持有期

```python
# 例如增加 60 天（季度）持有期
results = factor_analysis(factor_df, price_data, periods=(1, 5, 10, 20, 60), quantiles=10)
```

### 调整分位数

```python
# 改为 5 组
results = factor_analysis(factor_df, price_data, periods=(1, 5, 10), quantiles=5)
```

### 改为每日截面 MAD 缩尾

原版 `extreme_MAD` 使用全局静态阈值（对全时段所有股票一起计算）。改进版已改为每日截面缩尾（`groupby(level='trade_date').transform()`），直接运行 `fct_backtest改进版.py` 即可，详见 [改进版框架说明](说明文档/改进版框架说明.md)。

## 框架局限性

本框架定位为**因子研究/海选工具**，适合初步验证因子预测能力，但距离实盘交易有较大差距。主要局限包括：

- 不考虑交易成本（手续费、滑点、冲击成本）
- 未处理涨跌停/停牌等不可交易场景
- 无重叠采样（`auto`）会损失样本量；日历化调仓 + 多持有期则按重叠加仓重建真实策略收益（见 [改进版框架说明](说明文档/改进版框架说明.md) §5.4）
- 未计算换手率，无法评估调仓成本
- 组内默认等权，未考虑权重优化

> 原版"缺少行业和市值中性化处理"这一条已由改进版解决（`fct_backtest改进版.py` 内置市值+行业中性化）；"前视偏差""显著性 t 值虚高"也分别由每日截面预处理与 Newey-West 修正解决。

详见 [该框架的局限性](说明文档/该框架的局限性.md)。

## 文档索引

| 文档                                                        | 适合                               |
| ----------------------------------------------------------- | ---------------------------------- |
| [改进版框架说明](说明文档/改进版框架说明.md)                 | 了解改进版四大改进、新指标与用法   |
| [数据说明报告](说明文档/数据说明报告.md)                     | 初次接触，理解两份数据             |
| [数据透视与回测原理说明](说明文档/数据透视与回测原理说明.md) | 理解长表⇄宽表转换为什么是回测核心 |
| [回测流程图](说明文档/回测流程图.md)                         | 全局视角，原版 + 改进版 Mermaid 流程图 |
| [parquet文件格式](说明文档/parquet文件格式.md)               | 理解为什么量化用 Parquet 而非 CSV  |
| [该框架的局限性](说明文档/该框架的局限性.md)                 | 评估框架是否适合你的场景           |
