# -*- coding: utf-8 -*-
"""
构造加密货币因子：从 crypto_price_data.parquet 生成 crypto_fct_df.parquet。

因子清单（统一口径：收益 = 收盘价之比 − 1，按币种分组滚动）：
  mom_1d  单日涨跌幅 = close / close.shift(1) − 1
  mom_5d  过去 5 天收益率（动量）= close / close.shift(5) − 1
  mom_7d  过去 7 天收益率（动量）= close / close.shift(7) − 1
  mom_10d 过去 10 天收益率（动量）= close / close.shift(10) − 1
  mom_20d 过去 20 天收益率（动量）= close / close.shift(20) − 1

口径说明：
  - 加密无拆股/分红，直接使用原始 close（后复权等价原价），无需复权列；
  - 输出与 crypto_price_data.parquet 完全一致的 (trade_date, code) 双索引，
    便于回测脚本 pd.concat(..., join='inner') 直接对齐；
  - 每币首 n 天动量缺失（NaN），由回测框架的 fillna(0) 补成截面均值水平。
"""
import os
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_PRICE = os.path.join(ROOT, 'crypto_price_data.parquet')
OUT_FCT = os.path.join(ROOT, 'crypto_fct_df.parquet')

MOMENTUM_WINDOWS = (1, 5, 7, 10, 20)


def build_momentum_factors(price):
    """按币种分组计算动量收益，返回与 price 同索引的因子 DataFrame。"""
    close = price['close']
    factors = {}
    for n in MOMENTUM_WINDOWS:
        factors[f'mom_{n}d'] = close.groupby(level='code').transform(
            lambda s: s / s.shift(n) - 1)
    return pd.DataFrame(factors)


def main():
    price = pd.read_parquet(DATA_PRICE)
    print(f'读取 {DATA_PRICE}: {price.shape}（{price.index.get_level_values("code").nunique()} 币）')

    fct = build_momentum_factors(price)
    print('因子列:', list(fct.columns))
    print('各因子 NaN 数:', fct.isna().sum().to_dict())

    fct.to_parquet(OUT_FCT)
    print(f'已保存: {OUT_FCT}')


if __name__ == '__main__':
    main()
