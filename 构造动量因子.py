# -*- coding: utf-8 -*-
"""
经典动量因子构建
动量因子 = 过去N个交易日的收益率，即 (close_t / close_{t-N}) - 1
"""

import pandas as pd

# ============================================================
# 1. 读取数据 & 预处理（剔除ST、上市不足1年）
# ============================================================
data = pd.read_parquet("price_data.parquet")
df = data.copy()
mask = (~df['is_st']) & (df['trade_days'] >= 365)
df = df[mask].copy()
df.drop(['is_st', 'trade_days'], axis=1, inplace=True)

# ============================================================
# 2. 计算动量因子
# ============================================================
# 思路：
#   a) 将 close_adj 透视为宽表（行=日期，列=股票）
#   b) 对每只股票，计算过去N天的收益率：close / close.shift(N) - 1
#   c) 堆叠回长表格式，作为因子值

# 提取收盘价，透视成宽表
close_wide = df['close_adj'].unstack('code')  # index=trade_date, columns=code

# 按日期排序（确保 shift 操作正确）
close_wide = close_wide.sort_index()

# 构建多个窗口的动量因子
momentum_windows = {
    'mom_20d': 20,   # 20日动量（约1个月）
    'mom_60d': 60,   # 60日动量（约1个季度）
    'mom_120d': 120, # 120日动量（约半年）
}

# 用于收集各窗口的因子DataFrame
fct_dfs = []

for name, window in momentum_windows.items():
    # 计算 N 日收益率：T日的20日动量 = T日收盘价 / T-20日收盘价 - 1
    momentum = close_wide / close_wide.shift(window) - 1

    # 堆叠回长表：MultiIndex(trade_date, code)
    mom_series = momentum.stack()

    # 去除 NaN（每只股票前 window 天没有足够历史数据）
    mom_series = mom_series.dropna()

    # 重命名为因子名
    mom_series.name = name

    fct_dfs.append(mom_series)

# 合并所有窗口的因子，变成宽表（每列一个因子）
fct_df = pd.concat(fct_dfs, axis=1)  # columns: ['mom_20d', 'mom_60d', 'mom_120d']

# ============================================================
# 3. 保存因子数据（供回测框架读取）
# ============================================================
fct_df.to_parquet('fct_df.parquet')

print("因子构建完成！")
print(f"因子列: {fct_df.columns.tolist()}")
print(f"数据形状: {fct_df.shape}")
print(f"日期范围: {fct_df.index.get_level_values('trade_date').min()} ~ {fct_df.index.get_level_values('trade_date').max()}")
print(f"股票数量: {fct_df.index.get_level_values('code').nunique()}")
print("\n预览:")
print(fct_df.head(10))
print("\n描述性统计:")
print(fct_df.describe())
