# -*- coding: utf-8 -*-
"""
拉取市值与行业数据并写入 code/price_data.parquet
====================================================
基于米筐 rqdatac SDK（配置见 米筐配置.md），给 price_data.parquet 增加两列：
    - mkt_cap    : 总市值（元），来自 rqdatac 因子 'market_cap'（日频）
    - industry   : 申万一级行业名（如 '银行'），来自 rqdatac get_instrument_industry(source='sws', level=1)

行业采用"月度锚点 + 前向填充"：
    - 锚点 = 每个自然月的最后一个交易日（与月频调仓对齐）；
    - 月内行业视为不变，把锚点值前向填充到该月所有交易日；
    - 第一个锚点前补取首个交易日，保证全区间覆盖。

运行方式（必须用米筐环境）：
    /opt/anaconda3/envs/Ricequant_SDK/bin/python 构造市值行业数据.py

注意：
    - 运行前会自动备份原文件为 price_data_backup.parquet（验证通过后可删除）；
    - 若 price_data 已包含 mkt_cap 与 industry 两列则跳过。
"""

import os
import shutil
import time

import numpy as np
import pandas as pd
import rqdatac

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, 'price_data.parquet')
BACKUP_PATH = os.path.join(HERE, 'price_data_backup.parquet')
MV_CACHE = os.path.join(HERE, '_cache_mkt_cap.parquet')       # 中间缓存，成功写回后删除
IND_CACHE = os.path.join(HERE, '_cache_industry.parquet')

CHUNK = 1000          # 每批拉取的股票数
MKT_CAP_FACTOR = 'market_cap'   # rqdatac 因子名：总市值
INDUSTRY_SOURCE = 'sws'         # 申万行业分类
INDUSTRY_LEVEL = 1              # 一级行业


def to_rq_code(code):
    """本地代码 -> 米筐 order_book_id：'000001.SZ'->'000001.XSHE'，'600000.SH'->'600000.XSHG'"""
    sym, ex = str(code).rsplit('.', 1)
    if ex == 'SZ':
        return sym + '.XSHE'
    if ex == 'SH':
        return sym + '.XSHG'
    return str(code)  # 未知后缀原样返回（后续拉不到数据，留 NaN）


def to_local_code(rq_code):
    """米筐 order_book_id -> 本地代码"""
    return str(rq_code).replace('.XSHE', '.SZ').replace('.XSHG', '.SH')


def pull_market_cap(rq_codes, start_date, end_date):
    """拉取全区间每日总市值，返回 MultiIndex[trade_date, code] 的 Series（值=mkt_cap）"""
    frames = []
    for i in range(0, len(rq_codes), CHUNK):
        batch = rq_codes[i:i + CHUNK]
        t0 = time.time()
        df = rqdatac.get_factor(batch, MKT_CAP_FACTOR, start_date, end_date, expect_df=True)
        if df is None or len(df) == 0:
            print(f"  [市值] 批次 {i // CHUNK + 1}: {len(batch)} 只 -> 无数据，跳过")
            continue
        df = df.reset_index()                     # 列: order_book_id, date, market_cap
        df.columns = ['rq_code', 'trade_date', 'mkt_cap']
        df['code'] = df['rq_code'].map(to_local_code)
        frames.append(df[['trade_date', 'code', 'mkt_cap']])
        print(f"  [市值] 批次 {i // CHUNK + 1}: {len(batch)} 只 -> {len(df)} 行 ({time.time()-t0:.1f}s)")
    if not frames:
        raise RuntimeError('市值数据拉取为空，请检查 rqdatac license / 区间')
    out = pd.concat(frames, ignore_index=True)
    return out.set_index(['trade_date', 'code'])['mkt_cap']


def pull_industry_at(rq_codes, anchor_date):
    """拉取某锚点日期所有股票的申万一级行业，返回 {本地代码: 行业名}"""
    rows = []
    for i in range(0, len(rq_codes), CHUNK):
        batch = rq_codes[i:i + CHUNK]
        df = rqdatac.get_instrument_industry(batch, date=anchor_date,
                                             source=INDUSTRY_SOURCE, level=INDUSTRY_LEVEL)
        if df is None or len(df) == 0:
            continue
        df = df.reset_index()
        df.columns = ['rq_code', 'industry_code', 'industry']
        df['code'] = df['rq_code'].map(to_local_code)
        rows.append(df[['code', 'industry']])
    if not rows:
        return {}
    return pd.concat(rows, ignore_index=True).set_index('code')['industry'].to_dict()


def build_industry_series(rq_codes, anchors, full_index):
    """
    对每个锚点日期拉行业，拼接为 MultiIndex[trade_date, code] 的 Series，
    再前向填充到 full_index（完整交易日×股票网格）。
    """
    frames = []
    for a in anchors:
        t0 = time.time()
        mapping = pull_industry_at(rq_codes, a)
        if not mapping:
            print(f"  [行业] 锚点 {a.date()}: 无数据")
            continue
        codes = pd.Index(mapping.keys(), name='code')
        s = pd.Series(list(mapping.values()), index=codes, name='industry')
        s = s.reset_index().assign(trade_date=a).set_index(['trade_date', 'code'])['industry']
        frames.append(s)
        print(f"  [行业] 锚点 {a.date()}: {len(mapping)} 只 ({time.time()-t0:.1f}s)")
    if not frames:
        raise RuntimeError('行业数据拉取为空，请检查 rqdatac license')
    ind = pd.concat(frames)
    # reindex 到完整网格后按股票前向填充（月末值填充到月内）
    ind = pd.DataFrame({'industry': ind}).reindex(full_index)
    ind = ind.groupby(level='code').ffill()
    return ind['industry']


def main():
    t_all = time.time()
    rqdatac.init()
    print('米筐已连接（license 有效）')

    if not os.path.exists(DATA_PATH):
        raise FileNotFoundError(f'找不到 {DATA_PATH}')
    price = pd.read_parquet(DATA_PATH)
    print(f"原数据: {price.shape[0]:,} 行 × {price.shape[1]} 列 | "
          f"区间 {price.index.get_level_values('trade_date').min().date()} ~ "
          f"{price.index.get_level_values('trade_date').max().date()}")

    if {'mkt_cap', 'industry'}.issubset(price.columns):
        print('price_data 已包含 mkt_cap 与 industry 列，跳过拉取。'
              '如需强制重拉请先删除这两列。')
        return

    # 备份
    if not os.path.exists(BACKUP_PATH):
        shutil.copy2(DATA_PATH, BACKUP_PATH)
        print(f"已备份原文件 -> {os.path.basename(BACKUP_PATH)}")

    codes = price.index.get_level_values('code').unique().tolist()
    rq_codes = [to_rq_code(c) for c in codes]
    all_dates = price.index.get_level_values('trade_date').unique().sort_values()
    start_date, end_date = all_dates[0].date().isoformat(), all_dates[-1].date().isoformat()

    # ---- 1. 市值（有缓存则复用） ----
    if os.path.exists(MV_CACHE):
        print("\n== 市值：命中缓存，跳过拉取 ==")
        mv = pd.read_parquet(MV_CACHE)['mkt_cap']
    else:
        print("\n== 拉取总市值 ==")
        mv = pull_market_cap(rq_codes, start_date, end_date)
        mv.to_frame().to_parquet(MV_CACHE)
        print(f"市值结果已缓存 -> {os.path.basename(MV_CACHE)}")

    # ---- 2. 行业（月度锚点 + 前向填充，有缓存则复用） ----
    ds = pd.Series(all_dates)
    month_end_dates = ds.groupby([ds.dt.year, ds.dt.month]).last().tolist()
    anchors = sorted(set(month_end_dates) | {all_dates[0]})  # 补首个交易日，保证全区间覆盖
    print(f"\n== 申万一级行业（月度锚点，共{len(anchors)}个：{anchors[0].date()}~{anchors[-1].date()}） ==")
    if os.path.exists(IND_CACHE):
        print("== 行业：命中缓存，跳过拉取 ==")
        industry = pd.read_parquet(IND_CACHE)['industry']
    else:
        industry = build_industry_series(rq_codes, anchors, price.index)
        industry.to_frame().to_parquet(IND_CACHE)
        print(f"行业结果已缓存 -> {os.path.basename(IND_CACHE)}")

    # ---- 3. 合并写回 ----
    print("\n== 合并写回 ==")
    price['mkt_cap'] = mv.reindex(price.index)
    price['industry'] = industry.reindex(price.index)
    price.to_parquet(DATA_PATH)
    for c in (MV_CACHE, IND_CACHE):
        if os.path.exists(c):
            os.remove(c)
    print("已删除中间缓存文件")

    # ---- 4. 覆盖率诊断 ----
    n = len(price)
    mv_cov = price['mkt_cap'].notna().mean()
    ind_cov = price['industry'].notna().mean()
    print(f"\n写回完成 -> {DATA_PATH}")
    print(f"  mkt_cap  非空占比: {mv_cov:.2%}")
    print(f"  industry 非空占比: {ind_cov:.2%}")
    print(f"  industry 行业数  : {price['industry'].nunique()}")
    sample = price.loc[pd.IndexSlice['2024-01-05', ['000001.SZ', '000002.SZ']], ['mkt_cap', 'industry']]
    print("  抽查 2024-01-05:\n", sample)
    print(f"\n总耗时 {time.time()-t_all:.1f}s。备份文件 {os.path.basename(BACKUP_PATH)} 验证通过后可删除。")


if __name__ == '__main__':
    main()
