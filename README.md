# 单因子回测框架 (Single Factor Backtest Framework)

一个面向 A 股市场的单因子量化回测框架，支持因子构建、预处理、分层回测、绩效归因和可视化。

## 项目结构

```
fct_backtest/
├── fct_backtest注释版.py          # 主回测脚本（详细注释版，推荐阅读）
├── fct_backtest无冗余版.py         # 主回测脚本（精简版，适合快速运行）
├── 构造动量因子.py                 # 经典动量因子构建脚本（20/60/120日）
├── ic.py                          # IC 时间序列可视化脚本
├── price_data.parquet             # 行情数据（Git LFS 托管）
├── fct_df.parquet                 # 因子数据（Git LFS 托管）
|—— mom_fct_df.parquet			   # 20日动量因子（Git LFS 托管）
├── 图片/                          # 回测输出图表
│   ├── Figure_1~5.png             # 分层收益曲线 + 绩效仪表盘
│   └── IC时序图.png               # IC 时间序列图
├── 说明文档/
│   ├── 数据说明报告.md             # 两份数据的字段、统计特征详解
│   ├── 数据透视与回测原理说明.md    # 长表⇄宽表转换与回测原理拆解
│   ├── 回测流程图.md               # 9 步完整 Mermaid 流程图
│   ├── parquet文件格式.md          # Parquet vs CSV 对比及行业最佳实践
│   └── 该框架的局限性.md           # 框架优缺点分析，实盘适用性评估
└── README.md
```

## 功能特性

- **因子构建**：内置经典动量因子生成脚本，支持多窗口（20/60/120 日）
- **稳健的数据预处理**：MAD 缩尾去极值（中位数 ± 5.2×MAD）+ Z-Score 标准化
- **严格的样本池过滤**：自动剔除 ST 股票和上市不足一年的次新股
- **多持有期分层回测**：支持 1/5/10/20 天持有期，每个交易日按因子值等分为 10 组
- **完整的绩效指标体系**：IC、ICIR、分层累计收益、多空收益、夏普/索提诺/卡玛比率
- **无样本重叠**：通过 `iloc[::period]` 隔行采样，保证收益序列独立同分布
- **可视化输出**：分层累计收益曲线 + 因子绩效仪表盘（2×2 子图）
- **Excel 结果导出**：汇总统计 + 分位数收益 + IC 时间序列，三个 Sheet

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
```

运行后将输出：

- 控制台打印预处理后的数据概览和汇总统计表
- 弹出 matplotlib 图表窗口（分层收益曲线 + 绩效仪表盘）
- 生成 `单因子回测统计结果.xlsx`

### 3. 构建自定义因子

使用 `构造动量因子.py` 生成动量因子，或参考其逻辑构建你自己的因子：

```bash
python 构造动量因子.py
```

该脚本基于 `price_data.parquet` 中的后复权收盘价，生成 20 日、60 日、120 日三个窗口的动量因子，输出到 `fct_df.parquet`。

### 4. 切换测试因子

修改主回测脚本底部 `main` 中的因子选择：

```python
# 测试 mom_20d
factor_df = price_data['mom_20d']

# 或测试 mom_60d / mom_120d
factor_df = price_data['mom_60d']
```

### 5. 调整持有期

修改 `factor_analysis` 调用的 `periods` 参数：

```python
results = factor_analysis(factor_df, price_data, periods=(1, 5, 10, 20), quantiles=10)
```

### 6. 生成 IC 时序图

```bash
python ic.py
```

从回测输出的 Excel 中读取 IC 时间序列，绘制带统计标注的专业 IC 走势图。

## 回测流程

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

完整的 Mermaid 流程图见 [回测流程图](说明文档/回测流程图.md)。

## 核心方法论

### 因子预处理

| 步骤   | 方法     | 说明                                             |
| ------ | -------- | ------------------------------------------------ |
| 去极值 | MAD 缩尾 | `clip(median ± 5.2×MAD)`，比均值±3σ 更稳健 |
| 标准化 | Z-Score  | `(x − μ) / σ`，使因子跨截面可比             |
| 缺失值 | 填 0     | 标准化后填 0 即填充到均值水平                    |

### 分层回测

每个交易日按因子值将股票等分为 10 组（Q1~Q10），计算各组未来 1/5/10/20 天的平均收益率。为避免样本重叠，`iloc[::period]` 每隔 period 行取一个截面。

### 绩效指标

| 指标                 | 计算方式                           | 含义                           |
| -------------------- | ---------------------------------- | ------------------------------ |
| **IC**         | `mean(Spearman(因子, 未来收益))` | 因子预测能力，\|IC\| 越大越好  |
| **ICIR**       | `IC_mean / IC_std`               | IC 稳定性，越大越可靠          |
| **多空收益**   | Q10 累计收益 − Q1 累计收益        | 最高组做多、最低组做空的收益差 |
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

当前 `extreme_MAD` 使用全局静态阈值（对全时段所有股票一起计算）。如需改为每日截面缩尾（更符合行业惯例），可在函数外层按 `trade_date` 做 `groupby().transform()`。

## 框架局限性

本框架定位为**因子研究/海选工具**，适合初步验证因子预测能力，但距离实盘交易有较大差距。主要局限包括：

- 不考虑交易成本（手续费、滑点、冲击成本）
- 未处理涨跌停/停牌等不可交易场景
- 非重叠采样导致样本量大幅损失
- 缺少行业和市值中性化处理
- 未计算换手率，无法评估调仓成本

详见 [该框架的局限性](说明文档/该框架的局限性.md)。

## 文档索引

| 文档                                                        | 适合                               |
| ----------------------------------------------------------- | ---------------------------------- |
| [数据说明报告](说明文档/数据说明报告.md)                     | 初次接触，理解两份数据             |
| [数据透视与回测原理说明](说明文档/数据透视与回测原理说明.md) | 理解长表⇄宽表转换为什么是回测核心 |
| [回测流程图](说明文档/回测流程图.md)                         | 全局视角，9 步 Mermaid 流程图      |
| [parquet文件格式](说明文档/parquet文件格式.md)               | 理解为什么量化用 Parquet 而非 CSV  |
| [该框架的局限性](说明文档/该框架的局限性.md)                 | 评估框架是否适合你的场景           |
