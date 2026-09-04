# -*- coding: utf-8 -*-
"""
单因子回测框架（改进版）
=====================================================================
在原 fct_backtest注释版.py 基础上修复四大问题（详见 说明文档/改进版框架说明.md）：

【问题一】数据预处理存在"全局前视偏差"与"静态失真"
  - 原：MAD 去极值 / Z-score 标准化用全样本（全历史×全股票）统计量，回测"穿越"使用未来分布。
  - 改：先过滤样本池（去 ST / 次新），再按【每日截面】做 MAD 缩尾 + Z-score 标准化，统计量只用当日股票。

【问题二】调仓逻辑存在"日频绑定"与"过度交易幻觉"
  - 原：iloc[::period] 固定交易日间隔强行模拟持有期，月频因子无法适配。
  - 改：引入日历化调仓（auto / weekly / monthly / daily），收益按"调仓窗口"锚定
        （T 日因子 -> T+1 日买入 -> 下一调仓日 T+1 卖出），monthly 即为"月末因子值 -> 下月收益"。
        auto 模式严格复现原非重叠采样行为。

【问题三】评价体系缺失"预测能力"与"显著性"的严谨统计检验
  - 原：只算 Spearman IC，无回归斜率、无自相关修正。
  - 改：新增 Fama-MacBeth 每日截面线性回归（斜率即因子每变动 1 单位带来的收益），
        并对 IC / 多空收益 / FM 斜率序列做 Newey-West HAC 自相关修正，给出修正后 t 值。

【问题四】因子归因缺乏"风格剥离"（市值与行业中性化）
  - 原：全程未剥离市值/行业，收益混淆大小盘与行业贝塔。
  - 改：若数据含 mkt_cap / industry 列，逐调仓日对因子做截面回归
        factor ~ log(mkt_cap) + 行业哑变量，取残差作为中性化因子，
        同时输出原始 vs 中性化的 IC / 斜率 / 多空收益对比及"市值马甲"相关诊断。

依赖：pandas / numpy / scipy / matplotlib / pyarrow（读取 parquet）
运行：python fct_backtest改进版.py
"""

import os

import numpy as np
import pandas as pd
from scipy import stats
import matplotlib.pyplot as plt
import warnings
warnings.filterwarnings('ignore')

plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'PingFang SC', 'SimHei']
plt.rcParams['axes.unicode_minus'] = False

HERE = os.path.dirname(os.path.abspath(__file__))

###############################################################################
#### 配置参数（改这里 / 或通过环境变量 FCT_* 覆盖） ####
###############################################################################
# 直接改下面几行即可；命令行也可用环境变量覆盖，便于批量测试：
#   FCT_FACTOR=fct_1 FCT_REBALANCE=monthly FCT_PERIODS=1 FCT_NEUTRALIZE=None \
#   python fct_backtest改进版.py
FACTOR = os.environ.get('FCT_FACTOR', 'fct_2')                     # 待回测因子列名
REBALANCE = os.environ.get('FCT_REBALANCE', 'monthly')                # 'auto'|'weekly'|'monthly'|'daily'
# 默认持有期按调仓方式自适应：
#   auto/daily -> 天频 (1,5,10,20) 天；weekly/monthly -> 默认只测 1 个调仓窗口（持 1 周/1 个月）。
# 多窗口需显式 FCT_PERIODS（如月频 FCT_PERIODS=1,3,6,12 = 持有 1/3/6/12 个月）。
_PERIOD_DEFAULT = {'auto': (1, 5, 10, 20), 'daily': (1, 5, 10, 20),
                   'weekly': (1,), 'monthly': (1,)}
PERIODS = tuple(int(x) for x in os.environ.get(
    'FCT_PERIODS', ','.join(str(p) for p in _PERIOD_DEFAULT.get(REBALANCE, (1,)))).split(','))
QUANTILES = int(os.environ.get('FCT_QUANTILES', '10'))             # 分组数
_neutralize_env = os.environ.get('FCT_NEUTRALIZE', 'mktcap_industry')
NEUTRALIZE = None if _neutralize_env in ('None', 'none', '') else _neutralize_env
OUTPUT_PATH = os.path.join(HERE, '因子回测统计结果.xlsx')
# 图表输出：所有图表【始终】保存为 PNG 到 code/figs/（每次运行都会覆盖同名文件），
# 保证目录里永远是最新一次运行的完整图表；设 FCT_SHOW_FIGS=1 时额外弹窗显示。
# （旧参数 FCT_SAVE_FIGS=1 仍兼容，等价于"只保存不弹窗"，如今已是默认行为。）
FIGS_DIR = os.path.join(HERE, 'figs')
SHOW_FIGS = os.environ.get('FCT_SHOW_FIGS', '').lower() in ('1', 'true', 'yes')
if os.environ.get('FCT_SAVE_FIGS', '').lower() in ('1', 'true', 'yes'):
    SHOW_FIGS = False

DATA_PRICE = os.path.join(HERE, 'price_data.parquet')
DATA_FCT = os.path.join(HERE, 'fct_df.parquet')


###############################################################################
#### 数据处理函数 ####
###############################################################################

def extreme_MAD(dt, n=5.2):
    """每日截面 MAD 缩尾：dt 为某一交易日内所有股票的因子值 Series。

    统计量（中位数 / MAD）只使用当日股票，不使用未来样本，避免全局前视偏差。
    """
    median = dt.median()
    mad = (dt - median).abs().median()
    if mad == 0 or np.isnan(mad):
        return dt
    dt_up = median + n * mad
    dt_down = median - n * mad
    return dt.clip(dt_down, dt_up)


def standardize_z(dt):
    """每日截面 Z-score 标准化：dt 为某一交易日内所有股票的因子值 Series。"""
    std = dt.std()
    if std == 0 or np.isnan(std):
        return dt * 0.0  # 当日截面无差异 -> 无信息，标准化为 0
    return (dt - dt.mean()) / std


def preprocess_data(data):
    """样本池过滤：剔除 ST 股票与上市不足一年的次新股。"""
    df = data.copy()
    mask = (~df['is_st']) & (df['trade_days'] >= 365)
    df = df[mask].copy()
    df.drop(['is_st', 'trade_days'], axis=1, inplace=True)
    return df


def preprocess_factors(price_data, factor_names):
    """每日截面预处理：MAD 缩尾 + Z-score 标准化，缺失填 0（=截面均值水平）。

    先过滤样本池、后算截面统计量，保证统计量只反映可投资池（避免 ST 股污染分布）。
    """
    for col in factor_names:
        price_data[col] = price_data.groupby(level='trade_date')[col].transform(extreme_MAD)
        price_data[col] = price_data.groupby(level='trade_date')[col].transform(standardize_z)
    price_data[factor_names] = price_data[factor_names].fillna(0)
    return price_data


###############################################################################
#### 日历化调仓 ####
###############################################################################

def build_rebalance_dates(trade_dates, rebalance, period=None):
    """生成调仓日序列（升序 DatetimeIndex）。

    - 'auto'   : 每隔 period 个交易日取一个（非重叠，等价原 iloc[::period]，保留原行为）
    - 'daily'  : 每个交易日
    - 'weekly' : 每周最后一个交易日
    - 'monthly': 每个自然月最后一个交易日（与财报披露 / 月度再平衡对齐）
    """
    dates = pd.DatetimeIndex(sorted(trade_dates))
    if rebalance == 'daily':
        return dates
    if rebalance == 'auto':
        if period is None:
            raise ValueError("auto 模式必须提供 period")
        return dates[::period]
    if rebalance == 'weekly':
        s = pd.Series(dates)
        return pd.DatetimeIndex(s.groupby([s.dt.isocalendar().year, s.dt.isocalendar().week]).last())
    if rebalance == 'monthly':
        s = pd.Series(dates)
        return pd.DatetimeIndex(s.groupby([s.dt.year, s.dt.month]).last())
    raise ValueError(f"未知调仓方式: {rebalance}")


def rebalance_period_days(rebalance, period):
    """每个调仓窗口对应的自然交易日天数（用于年化口径）。"""
    if rebalance == 'monthly':
        return 21
    if rebalance == 'weekly':
        return 5
    return int(period)


def _strategy_period_days(rebalance, period):
    """策略窗口收益率序列的年化折算天数。

    auto（非重叠）：每段窗口即持有期，折 period 天；
    日历化调仓：策略收益序列按"1 个调仓窗口"采样（daily=1 天 / weekly=5 / monthly=21），
      与持有期 period 无关——重叠场景下若仍按 period 折算会把总天数虚增 period 倍、年化失真。
    """
    if rebalance == 'auto':
        return int(period)
    return rebalance_period_days(rebalance, 1)


def period_unit(rebalance):
    """持有期标签单位：auto/daily -> 天，weekly -> 周，monthly -> 月。"""
    return {'monthly': '月', 'weekly': '周'}.get(rebalance, '天')


def period_file_suffix(rebalance):
    """持有期文件后缀（ASCII，便于跨平台）：auto/daily -> d，weekly -> w，monthly -> m。"""
    return {'monthly': 'm', 'weekly': 'w'}.get(rebalance, 'd')


def factor_analysis(factor_series, price_df, periods=(1, 5, 10, 20), quantiles=10,
                    rebalance='auto'):
    """核心回测：基于调仓窗口计算非重叠持仓收益，并做每日截面分组。

    时间轴约定（无前视偏差）：
      调仓日 T 收盘已知因子值 -> 用 T+1 日收盘价模拟"次日买入"
      -> period 个调仓窗口之后的调仓日次日收盘卖出 -> 收益 = 卖出/买入 - 1，标签贴在 T 日。
      auto 模式：R 每隔 period 交易日取一个，卖出 = 下一 R 的次日 -> 严格复现原 shift(-period-1)/shift(-1)。
      monthly 模式：R = 每月最后交易日，period=1 即"月末因子值 -> 下月收益"的非重叠月频窗口；
                    period=2 表示每月调仓、持有 2 个月（卖出 = 第 2 个下月末的次日）。weekly 同理。
      period>1 的日历化调仓会产生重叠窗口，绩效指标（累计/多空/年化/夏普/回撤）由
      _overlapping_strategy_returns 按"全持仓·等权·重叠加仓"重建，见 _factor_metrics。
    返回 (results, ctx)，ctx = {'price_aligned', 'reb_dates: {period: R}}。
    """
    price_aligned = pd.pivot_table(
        price_df['close_adj'].reset_index(), index='trade_date', columns='code', values='close_adj')
    all_dates = price_aligned.index

    results = {}
    reb_by_period = {}
    for period in periods:
        reb_dates = build_rebalance_dates(all_dates, rebalance, period)
        reb_by_period[period] = reb_dates
        # auto 的 R 已被抽稀（每隔 period 天），卖出 = 下一 R；其余模式卖出 = period 个窗口后的 R
        exit_shift = 1 if rebalance == 'auto' else period
        next_reb = pd.Series(reb_dates).shift(-exit_shift).to_numpy()
        mask = ~pd.isna(next_reb)
        anchor = reb_dates[mask]                       # 存在卖出日的调仓日
        exit_dates = pd.DatetimeIndex(next_reb[mask])  # 对应的卖出调仓日

        # 买入 = 调仓日次日，卖出 = 卖出调仓日的次日
        anchor_pos = all_dates.get_indexer(anchor)
        exit_pos = all_dates.get_indexer(exit_dates)
        ok = (anchor_pos + 1 < len(all_dates)) & (exit_pos + 1 < len(all_dates))
        anchor, exit_dates = anchor[ok], exit_dates[ok]
        entry_dates = all_dates[anchor_pos[ok] + 1]
        sell_dates = all_dates[exit_pos[ok] + 1]

        buy = price_aligned.loc[entry_dates].to_numpy()      # (n_win, n_stock)
        sell = price_aligned.loc[sell_dates].to_numpy()
        ret = pd.DataFrame(sell / buy - 1, index=anchor, columns=price_aligned.columns)
        # ret 行索引 = 调仓日（因子已知日），列 = 股票代码

        factor_df = factor_series.reset_index()
        factor_df.columns = ['trade_date', 'code', 'factor']
        # 携带风格列（市值/行业），供中性化使用
        style_cols = [c for c in ('mkt_cap', 'industry') if c in price_df.columns]
        if style_cols:
            style = price_df[style_cols].reset_index()
            factor_df = pd.merge(factor_df, style, on=['trade_date', 'code'], how='left')
        ret_long = ret.stack()                               # MultiIndex [trade_date, code]
        ret_long.name = f'return_{period}d'
        merged = pd.merge(factor_df, ret_long,
                          left_on=['trade_date', 'code'], right_index=True)
        merged = merged.dropna(subset=['factor', f'return_{period}d'])
        merged = merged.sort_values('trade_date')

        if len(merged) > 0:
            results[period] = merged

    # 返回价格矩阵与各持有期的完整调仓日序列，供"重叠加仓"真实策略收益重建使用
    return results, {'price_aligned': price_aligned, 'reb_dates': reb_by_period}


###############################################################################
#### 统计检验：Fama-MacBeth 截面回归 + Newey-West 自相关修正 ####
###############################################################################

def newey_west_se(x, lags=None, min_lags=0):
    """Newey-West HAC 修正标准误（针对均值），修正序列自相关导致的 t 值虚高。

    x: 一维收益/斜率/IC 序列。返回 mean(x) 的 HAC 标准误。
    lag 选择：lags = floor(4 * (n/100)^(2/9))，Bartlett 权重；
             重叠窗口序列存在 MA(period-1) 结构，可用 min_lags 强制滞后阶数不低于重叠长度。
    """
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 3:
        return np.nan
    if lags is None:
        lags = int(np.floor(4 * (n / 100.0) ** (2.0 / 9.0)))
    lags = max(lags, int(min_lags))
    lags = max(0, min(lags, n - 2))
    xc = x - x.mean()
    var = np.mean(xc * xc)                       # gamma_0
    for k in range(1, lags + 1):
        gamma_k = np.mean(xc[k:] * xc[:-k])
        var += 2 * (1 - k / (lags + 1)) * gamma_k  # Bartlett 权重
    return np.sqrt(var / n)


def fama_macbeth_regression(df, factor_col, ret_col, control_cols=None, min_lags=0):
    """逐调仓日截面 OLS：ret ~ factor (+ controls)，收集斜率做 Newey-West t 检验。

    返回: mean_slope(每单位因子收益)、slope_bp(×10000，基点)、t_naive、t_nw、n_dates、
          slope_series(逐调仓日斜率序列，供可视化)。
    min_lags: 重叠窗口时 NW 滞后阶数下限（覆盖 MA(period-1) 自相关）。
    """
    slope_dates, slopes = [], []
    for date, sub in df.groupby('trade_date'):
        sub = sub.dropna(subset=[factor_col, ret_col])
        if len(sub) < 10:
            continue
        y = sub[ret_col].to_numpy(dtype=float)
        X = np.column_stack([np.ones(len(sub)), sub[factor_col].to_numpy(dtype=float)])
        if control_cols:
            for c in control_cols:
                v = sub[c].to_numpy(dtype=float)
                if np.isnan(v).all():
                    continue
                X = np.column_stack([X, v])
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        slopes.append(beta[1])                   # factor 系数
        slope_dates.append(date)
    slopes = np.array(slopes)
    slope_series = (pd.Series(slopes, index=pd.Index(slope_dates, name='trade_date'))
                    if len(slopes) else pd.Series(dtype=float))
    if len(slopes) < 3:
        return {'mean_slope': np.nan, 'slope_bp': np.nan,
                't_naive': np.nan, 't_nw': np.nan, 'n_dates': len(slopes),
                'slope_series': slope_series}
    mean_slope = slopes.mean()
    se_naive = slopes.std(ddof=1) / np.sqrt(len(slopes))
    se_nw = newey_west_se(slopes, min_lags=min_lags)
    return {
        'mean_slope': mean_slope,
        'slope_bp': mean_slope * 10000,
        't_naive': mean_slope / se_naive if se_naive else np.nan,
        't_nw': mean_slope / se_nw if se_nw and se_nw > 0 else np.nan,
        'n_dates': len(slopes),
        'slope_series': slope_series,
    }


###############################################################################
#### 风格剥离：市值 + 行业中性化 ####
###############################################################################

def neutralize_factor(df, factor_col, mkt_cap_col='mkt_cap', industry_col='industry',
                      mode='mktcap_industry'):
    """逐调仓日对因子做截面回归取残差（Barra 风格中性化）。

    mode:
      'mktcap_only'       : factor ~ log(mkt_cap)
      'mktcap_industry'   : factor ~ log(mkt_cap) + 行业哑变量
    返回新增列 factor_col + '_neutralized'。缺风格数据的行留 NaN（调用方剔除）。
    """
    def _one_day(sub):
        f = sub[factor_col]
        out = pd.Series(np.nan, index=sub.index)
        parts = []
        if mode in ('mktcap_only', 'mktcap_industry') and mkt_cap_col in sub.columns:
            parts.append(np.log(sub[mkt_cap_col]).rename('lmcap'))
        if mode == 'mktcap_industry' and industry_col in sub.columns:
            # drop_first=True：去掉一个行业基准类，避免与下方 np.ones 截距构成"哑变量陷阱"
            # （秩亏设计；残差与旧版全哑变量写法在数学上完全一致，已实测 max|Δ|~1e-14）
            parts.append(pd.get_dummies(sub[industry_col].fillna('_未知_'), prefix='ind',
                                        drop_first=True))
        if not parts:
            return f  # 无风格列，原样返回
        X = pd.concat(parts, axis=1).replace([np.inf, -np.inf], np.nan)
        keep = X.notna().all(axis=1) & f.notna()
        if keep.sum() < 10:
            return out
        y = f[keep].to_numpy(dtype=float)
        Xm = np.column_stack([np.ones(y.size), X[keep].to_numpy(dtype=float)])
        beta, *_ = np.linalg.lstsq(Xm, y, rcond=None)
        out.loc[keep] = y - Xm @ beta
        return out

    df[factor_col + '_neutralized'] = df.groupby('trade_date', group_keys=False).apply(_one_day)
    return df


###############################################################################
#### 单因子表现分析 ####
###############################################################################

def _quantile_transform(s, n):
    """按日截面等频分组，Q1~Qn；当日唯一直过少时整组置 NaN（该日被后续剔除）。"""
    try:
        return pd.qcut(s, n, labels=False, duplicates='drop') + 1
    except Exception:
        return pd.Series(np.nan, index=s.index)


def _overlapping_strategy_returns(d, price_aligned, reb_dates, period, rebalance):
    """按"全持仓·等权·重叠加仓"重建真实策略的逐窗口收益（重叠窗口专用）。

    背景：daily/weekly/monthly + period>1 时相邻持有窗口重叠，若对每个调仓日的整段
    持有期收益直接 cumprod，重叠部分被重复复利，得到天文数字的虚假累计/多空收益
    （实测 daily 20 天曾算出 -844 万%、weekly 16 周 -5.5 万%）。本函数重建真实口径：
      策略在每个调仓窗口持有最近 s=period 个开仓日选出的等权组合（各 1/s 权重，全持仓），
      窗口 j 的策略收益 w_j = (1/s)·Σ_{i=j-s+1..j} R[i,j]，
      其中 R[i,j] = 第 i 个开仓日选出的组合在窗口 j 的等权收益
      （窗口 j 收益 = 调仓日 j+1 的次日收盘 / 调仓日 j 的次日收盘 − 1）。
    少数开仓日无有效分组时按实际形成仓位归一化（视为资本未投足而回补）。
    无重叠场景（auto / period=1）不应调用（s=1 时本式退化为单组自身窗口收益，与旧逻辑一致）。

    返回 {quantile: pd.Series(策略窗口收益, index=对应调仓日 R[j])}。
    """
    s = 1 if rebalance == 'auto' else period
    all_dates = price_aligned.index
    R = pd.DatetimeIndex(reb_dates)
    pos = all_dates.get_indexer(R)
    ok = (pos >= 0) & (pos + 1 < len(all_dates))
    R = R[ok]
    boundary = all_dates[pos[ok] + 1]                 # 每个调仓日的次日（窗口左边界买入价）
    prices = price_aligned.loc[boundary]              # (n_R, n_stock)
    W = prices.iloc[1:].to_numpy(dtype=float) / prices.iloc[:-1].to_numpy(dtype=float) - 1
    n_R = R.shape[0]
    m = n_R - s                                       # 完整开仓日数（存在卖出日的调仓日）
    if m < s:                                         # 窗口数不足，无法形成满仓策略
        return {}
    n_stock = prices.shape[1]
    codes = list(price_aligned.columns)
    stock_idx = {c: i for i, c in enumerate(codes)}

    dq = d[['trade_date', 'code', 'quantile']].dropna(subset=['quantile'])
    dq = dq[dq['trade_date'].isin(R)]                 # 只保留落在调仓日上的仓位
    if len(dq) == 0:
        return {}
    anchor_pos = {a: i for i, a in enumerate(R)}
    out = {}
    for qq in sorted(dq['quantile'].unique()):
        sub = dq[dq['quantile'] == qq]
        row_ids = np.array([anchor_pos[a] for a in sub['trade_date']])
        col_ids = np.array([stock_idx[c] for c in sub['code']])
        wgt = 1.0 / sub.groupby('trade_date')['code'].transform('count').to_numpy()  # 组内等权
        M = np.zeros((n_R, n_stock))
        M[row_ids, col_ids] = wgt
        row_norm = np.bincount(row_ids, minlength=n_R) > 0   # 该调仓日是否形成了仓位
        cumM = np.vstack([np.zeros((1, n_stock)), np.cumsum(M, axis=0)])
        cumN = np.concatenate([[0], np.cumsum(row_norm.astype(int))])
        jj = np.arange(s - 1, m)                      # 策略窗口 j = s-1 .. m-1
        lo = jj - (s - 1)                             # j-s+1
        hi = jj + 1
        cnt = cumN[hi] - cumN[lo]                     # 窗口内有效仓位数
        B = (cumM[hi] - cumM[lo]) / cnt[:, None]      # (|jj|, n_stock)，组内等权、跨期等权
        Wb = W[jj]                                    # 对应窗口的股票收益
        Wnan = ~np.isfinite(Wb)                       # 停牌/缺失边界价 -> 该窗口不参与（未知）
        num = (B * np.where(Wnan, 0.0, Wb)).sum(axis=1)
        den = (B * (~Wnan)).sum(axis=1)
        w = np.full(len(jj), np.nan)
        good = (cnt > 0) & np.isfinite(den) & (den > 0)
        if good.any():
            w[good] = num[good] / den[good]
        out[qq] = pd.Series(w, index=pd.Index(R[jj], name='trade_date'), name=qq)
    return out


def _factor_metrics(df, period, factor_col, quantiles=10,
                    price_aligned=None, reb_dates=None, rebalance='auto'):
    """对指定因子列计算一套完整指标（分层收益 / IC / FM 回归 / NW t 值）。

    分层收益在重叠场景（日历化调仓 + period>1）用 _overlapping_strategy_returns 重建
    真实策略收益，避免对重叠窗口连乘产生虚假累计；无重叠（auto / period=1）时保持旧逻辑。
    """
    ret_col = f'return_{period}d'
    d = df.dropna(subset=[factor_col, ret_col]).copy()
    d['quantile'] = d.groupby('trade_date')[factor_col].transform(
        lambda x: _quantile_transform(x, quantiles))
    s = 1 if rebalance == 'auto' else period

    # 分层收益
    if s > 1 and price_aligned is not None and reb_dates is not None and len(d):
        strat = _overlapping_strategy_returns(d, price_aligned, reb_dates, period, rebalance)
        q = pd.DataFrame(strat).sort_index() if strat else pd.DataFrame(dtype=float)
    else:
        q = d.groupby(['trade_date', 'quantile'])[ret_col].mean().reset_index()
        q = pd.pivot_table(q, index='trade_date', columns='quantile', values=ret_col)
        q = q.sort_index()
    q_cum = q.add(1).cumprod().sub(1).iloc[-1] if len(q) else pd.Series(dtype=float)

    out = {'returns_pivot': q, 'quantile_returns': q_cum}
    if len(q_cum) < 2:
        out.update({'long_short_return': np.nan, 'ls_series': pd.Series(dtype=float),
                    'ic': np.nan, 'ic_ir': np.nan, 'ic_series': pd.Series(dtype=float),
                    'ic_t_naive': np.nan, 'ic_t_nw': np.nan,
                    'ls_t_naive': np.nan, 'ls_t_nw': np.nan,
                    'fm': fama_macbeth_regression(d, factor_col, ret_col, min_lags=s - 1)})
        return out

    q_min, q_max = q_cum.index.min(), q_cum.index.max()
    out['long_short_return'] = q_cum[q_max] - q_cum[q_min]
    ls_series = (q[q_max] - q[q_min]).dropna()
    out['ls_series'] = ls_series
    # 重叠窗口的收益序列存在 MA(period-1) 自相关，NW 滞后阶数需覆盖重叠长度
    out['ls_t_naive'] = ls_series.mean() / (ls_series.std(ddof=1) / np.sqrt(len(ls_series))) \
        if len(ls_series) > 2 and ls_series.std(ddof=1) > 0 else np.nan
    out['ls_t_nw'] = ls_series.mean() / newey_west_se(ls_series, min_lags=s - 1) \
        if len(ls_series) >= 3 else np.nan

    # IC 及其检验
    ic_series = d.groupby('trade_date').apply(
        lambda x: stats.spearmanr(x[factor_col], x[ret_col]).correlation
        if len(x) > 5 else np.nan).dropna()
    out['ic_series'] = ic_series
    if len(ic_series) == 0:
        out.update({'ic': np.nan, 'ic_ir': np.nan, 'ic_t_naive': np.nan, 'ic_t_nw': np.nan})
    else:
        ic = ic_series.mean()
        out['ic'] = ic
        out['ic_ir'] = ic / ic_series.std() if ic_series.std() > 0 else np.nan
        out['ic_t_naive'] = ic / (ic_series.std(ddof=1) / np.sqrt(len(ic_series))) \
            if ic_series.std(ddof=1) > 0 else np.nan
        out['ic_t_nw'] = ic / newey_west_se(ic_series, min_lags=s - 1)

    # Fama-MacBeth 回归
    out['fm'] = fama_macbeth_regression(d, factor_col, ret_col, min_lags=s - 1)
    return out


def analyze_factor_performance(res_, neutralize=None, quantiles=10,
                               mkt_cap_col='mkt_cap', industry_col='industry',
                               price_aligned=None, reb_dates=None, rebalance='auto'):
    """对每个持有期计算原始因子指标；若开启中性化，再计算中性化因子指标与诊断。"""
    perform_ = {}
    for period, df in res_.items():
        df = df.copy()
        pctx = {'price_aligned': price_aligned, 'reb_dates': (reb_dates or {}).get(period)}
        # 原始因子
        df['quantile'] = df.groupby('trade_date')['factor'].transform(
            lambda x: _quantile_transform(x, quantiles))
        raw = _factor_metrics(df, period, 'factor', quantiles, rebalance=rebalance, **pctx)
        entry = {
            'quantile_returns': raw['quantile_returns'],
            'long_short_return': raw['long_short_return'],
            'ic': raw['ic'],
            'ic_ir': raw['ic_ir'],
            'ic_series': raw['ic_series'],
            'data': df,
            'metrics': raw,
        }

        # 中性化
        if neutralize:
            missing = [c for c in (mkt_cap_col,) if c not in df.columns]
            if neutralize == 'mktcap_industry' and industry_col not in df.columns:
                missing.append(industry_col)
            if missing:
                print(f"[警告] 缺少风格列 {missing}，中性化降级为 None")
            else:
                df = neutralize_factor(df, 'factor', mkt_cap_col, industry_col, mode=neutralize)
                neut_df = df.dropna(subset=['factor_neutralized'])
                neut = _factor_metrics(neut_df, period, 'factor_neutralized', quantiles,
                                       rebalance=rebalance, **pctx)
                entry['neutralized'] = neut
                # 市值"马甲"诊断：因子与 log(市值) 的日均秩相关
                diag = df.dropna(subset=['factor', mkt_cap_col])
                entry['mkt_cap_corr'] = diag.groupby('trade_date').apply(
                    lambda x: stats.spearmanr(x['factor'], np.log(x[mkt_cap_col])).correlation
                    if len(x) > 5 else np.nan).mean()

        perform_[period] = entry
    return perform_


def effective_metrics(perf, neutralize):
    """中性化开启时返回中性化指标，否则返回原始指标。"""
    return perf.get('neutralized', perf['metrics']) if neutralize else perf['metrics']


###############################################################################
#### 方向判断 / 组合指标 / 汇总输出 ####
###############################################################################

def determine_factor_direction(perfo_, neutralize):
    """依据有效因子（中性化或原始）的多空收益判断方向。"""
    direction_info = {}
    for period, perf in perfo_.items():
        metrics = effective_metrics(perf, neutralize)
        ls = metrics['long_short_return']
        if ls > 0:
            direction, target_quantile = '正向因子', metrics['quantile_returns'].index.max()
        else:
            direction, target_quantile = '负向因子', metrics['quantile_returns'].index.min()
        direction_info[period] = {
            'direction': direction, 'target_quantile': target_quantile,
            'long_short_return': ls}
    return direction_info


def calculate_portfolio_metrics(returns_series, period_days=1):
    """组合指标：累计/年化收益、回撤、夏普、索提诺、卡玛。"""
    if len(returns_series) == 0:
        return {}
    returns_series = returns_series.dropna()
    if len(returns_series) == 0:
        return {}

    cumulative_curve = (1 + returns_series).cumprod()
    cumulative_return = cumulative_curve.iloc[-1] - 1

    total_days = len(returns_series) * period_days
    annual_return = (1 + cumulative_return) ** (252 / total_days) - 1 if total_days > 0 else 0

    running_max = cumulative_curve.expanding().max()
    drawdown = (cumulative_curve - running_max) / running_max
    max_drawdown = abs(drawdown.min())

    annual_vol = returns_series.std(ddof=1) * np.sqrt(252 / period_days) \
        if len(returns_series) > 1 and period_days > 0 else 0
    sharpe = annual_return / annual_vol if annual_vol > 0 else 0

    downside = returns_series[returns_series < 0]
    downside_vol = downside.std(ddof=1) * np.sqrt(252 / period_days) \
        if len(downside) > 1 and period_days > 0 else 0
    sortino = annual_return / downside_vol if downside_vol > 0 else 0

    calmar = annual_return / max_drawdown if max_drawdown > 0 else 0

    return {
        '累计收益率': cumulative_return,
        '年化收益率': annual_return,
        '最大回撤': -max_drawdown,
        '夏普比率': sharpe,
        '索提诺比率': sortino,
        '卡玛比率': calmar,
    }


def generate_summary_statistics(perform_, neutralize, rebalance, periods,
                                output_path=OUTPUT_PATH):
    """生成汇总统计并写 Excel（含 FM 回归 / NW 检验、中性化诊断 Sheet）。"""
    direction_info = determine_factor_direction(perform_, neutralize)
    summary_data = []

    for period in periods:
        if period not in perform_:
            continue
        perf = perform_[period]
        metrics = effective_metrics(perf, neutralize)
        direction = direction_info[period]['direction']
        target_q = direction_info[period]['target_quantile']
        ret_col = f'return_{period}d'

        q = metrics['returns_pivot']
        target_returns = q[target_q].dropna() if target_q in q.columns else pd.Series(dtype=float)
        # 重叠窗口下策略收益序列按"1 个调仓窗口"采样，年化口径见 _strategy_period_days
        port = calculate_portfolio_metrics(target_returns, _strategy_period_days(rebalance, period))

        fm = metrics['fm']
        row = {
            f'持有期({period_unit(rebalance)})': period,
            '调仓方式': rebalance,
            '因子方向': direction,
            'IC': perf['ic'] if not neutralize else metrics['ic'],
            'ICIR': perf['ic_ir'] if not neutralize else metrics['ic_ir'],
            'IC_t': metrics['ic_t_naive'],
            'IC_t_NW': metrics['ic_t_nw'],
            'FM斜率(BP)': fm['slope_bp'],
            'FM_t_naive': fm['t_naive'],
            'FM_t_NW': fm['t_nw'],
            '多空收益': metrics['long_short_return'],
            '多空_t_NW': metrics['ls_t_nw'],
            '累计收益率': port.get('累计收益率'),
            '年化收益率': port.get('年化收益率'),
            '最大回撤': port.get('最大回撤'),
            '夏普比率': port.get('夏普比率'),
        }
        if neutralize and 'neutralized' in perf:
            raw_fm = perf['metrics']['fm']
            row.update({
                '原始IC': perf['ic'],
                '原始FM斜率(BP)': raw_fm['slope_bp'],
                '原始多空收益': perf['metrics']['long_short_return'],
                '中性化IC': metrics['ic'],
                '中性化FM斜率(BP)': fm['slope_bp'],
                '中性化多空收益': metrics['long_short_return'],
                '因子vs市值相关': perf.get('mkt_cap_corr'),
            })
        summary_data.append(row)

    summary_df = pd.DataFrame(summary_data)
    if summary_df.empty:
        return summary_df

    try:
        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            summary_df.to_excel(writer, sheet_name='汇总统计', index=False)

            # 分位数收益
            quantile_rows = []
            for period in periods:
                if period not in perform_:
                    continue
                q_series = effective_metrics(perform_[period], neutralize)['quantile_returns']
                row = {f'持有期({period_unit(rebalance)})': period}
                for qq, ret in q_series.items():
                    row[f'Q{qq}'] = ret
                quantile_rows.append(row)
            pd.DataFrame(quantile_rows).to_excel(writer, sheet_name='分位数收益', index=False)

            # IC 时间序列
            ic_rows = []
            for period in periods:
                if period not in perform_:
                    continue
                eff = effective_metrics(perform_[period], neutralize)
                for date, v in eff['ic_series'].items():
                    ic_rows.append({f'持有期({period_unit(rebalance)})': period, '日期': date, 'IC值': v})
            if ic_rows:
                pd.DataFrame(ic_rows).to_excel(writer, sheet_name='IC时间序列', index=False)

            # FM 回归与 NW 检验
            fm_rows = []
            for period in periods:
                if period not in perform_:
                    continue
                eff = effective_metrics(perform_[period], neutralize)
                fm = eff['fm']
                fm_rows.append({
                    f'持有期({period_unit(rebalance)})': period,
                    'FM斜率(每单位收益)': fm['mean_slope'],
                    'FM斜率(BP)': fm['slope_bp'],
                    'FM_t_naive': fm['t_naive'],
                    'FM_t_NW': fm['t_nw'],
                    '截面数': fm['n_dates'],
                    'IC_t_naive': eff['ic_t_naive'],
                    'IC_t_NW': eff['ic_t_nw'],
                    '多空_t_naive': eff['ls_t_naive'],
                    '多空_t_NW': eff['ls_t_nw'],
                })
            if fm_rows:
                pd.DataFrame(fm_rows).to_excel(writer, sheet_name='FM回归与NW检验', index=False)

            # 中性化诊断
            if neutralize:
                diag_rows = []
                for period in periods:
                    if period not in perform_ or 'neutralized' not in perform_[period]:
                        continue
                    perf = perform_[period]
                    raw, neut = perf['metrics'], perf['neutralized']
                    diag_rows.append({
                        f'持有期({period_unit(rebalance)})': period,
                        '原始IC': perf['ic'],
                        '中性化IC': neut['ic'],
                        '原始FM斜率(BP)': raw['fm']['slope_bp'],
                        '中性化FM斜率(BP)': neut['fm']['slope_bp'],
                        '原始多空收益': raw['long_short_return'],
                        '中性化多空收益': neut['long_short_return'],
                        '因子vs市值相关': perf.get('mkt_cap_corr'),
                    })
                if diag_rows:
                    pd.DataFrame(diag_rows).to_excel(writer, sheet_name='中性化诊断', index=False)

        print(f"统计结果已保存到: {output_path}")
    except Exception as e:
        print(f"保存Excel文件时出错: {e}")

    return summary_df


###############################################################################
#### 可视化 ####
###############################################################################

def _finish_fig(fig, name):
    """统一收尾：始终保存 PNG 到 code/figs/；FCT_SHOW_FIGS=1 时额外弹窗显示。"""
    os.makedirs(FIGS_DIR, exist_ok=True)
    path = os.path.join(FIGS_DIR, name)
    fig.savefig(path, dpi=150, bbox_inches='tight')
    print(f'  图表已保存 -> {path}')
    if SHOW_FIGS:
        plt.show()
    plt.close(fig)


def _cum_quantiles(metrics):
    """由每日分位收益透视表算累计净值曲线：trade_date × quantile 的 (1+r).cumprod()。"""
    q = metrics['returns_pivot']
    if q is None or len(q) == 0:
        return pd.DataFrame(dtype=float)
    return q.add(1).cumprod()


def _quantile_colors():
    """Q1~Q10 统一配色：Bottom 蓝、Top 红、中间取柔和色板。"""
    colors = {1: '#3333FF', 10: '#FF3333'}
    middle = ['#FF6B6B', '#4ECDC4', '#FFD166', '#06D6A0',
              '#118AB2', '#EF476F', '#7B68EE', '#20B2AA']
    for idx, qq in enumerate(range(2, 10)):
        colors.setdefault(qq, middle[idx] if idx < len(middle) else None)
    return colors


def plot_quantile_returns_separate(perform_, neutralize=None):
    """每个持有期的分层累计收益曲线（Top/Bottom 组加粗；开启中性化时叠加虚线对比）。

    累计曲线直接基于绩效指标里的 returns_pivot（重叠窗口时已是重建后的真实策略收益），
    与汇总统计的累计口径保持一致，不再重复算 q。
    """
    colors = _quantile_colors()
    for period, perf in perform_.items():
        cum = _cum_quantiles(perf['metrics'])
        if len(cum) == 0:
            continue

        # 中性化因子的分层累计（虚线）
        neut_cum = None
        if neutralize and 'neutralized' in perf:
            neut_cum = _cum_quantiles(perf['neutralized'])

        fig, ax = plt.subplots(figsize=(14, 8))
        for qq in sorted(cum.columns):
            if qq in (1, 10):
                label = f'Top组(Q{qq})' if qq == 10 else f'Bottom组(Q{qq})'
                ax.plot(cum.index, cum[qq], label=label, color=colors[qq],
                        linewidth=3.0, alpha=1.0)
            elif colors.get(qq):
                ax.plot(cum.index, cum[qq], label=f'Q{qq}', color=colors[qq],
                        linewidth=1.5, alpha=0.8)
            if neut_cum is not None and qq in neut_cum.columns:
                ax.plot(neut_cum.index, neut_cum[qq], linestyle='--', linewidth=1.2,
                        color=colors[qq], alpha=0.45)

        if neut_cum is not None:
            ax.plot([], [], linestyle='--', color='gray', alpha=0.6, label='——虚线 = 中性化后')

        suffix = '（实线=原始 / 虚线=中性化）' if neut_cum is not None else ''
        ax.set_title(f'{period}{period_unit(REBALANCE)} - 分层收益（{REBALANCE}调仓）{suffix}',
                     fontsize=16, fontweight='bold')
        ax.legend(bbox_to_anchor=(1.05, 1), loc='upper left', fontsize=9, framealpha=0.9)
        ax.grid(True, alpha=0.3)
        if len(cum) > 10:
            n_ticks = min(8, len(cum))
            ticks = np.linspace(0, len(cum) - 1, n_ticks, dtype=int)
            ax.set_xticks(cum.index[ticks])
            ax.set_xticklabels([d.strftime('%Y-%m-%d') for d in cum.index[ticks]], rotation=45, fontsize=10)
        fig.tight_layout()
        _finish_fig(fig, f'quantile_returns_{period}{period_file_suffix(REBALANCE)}.png')


def plot_factor_performance(perfor_, neutralize=None):
    """2×2 绩效仪表盘：多空收益 / IC / 分位收益 / IC 时序。"""
    periods = sorted(perfor_.keys())
    fig, axes = plt.subplots(2, 2, figsize=(15, 10))
    axes = axes.flatten()

    eff = [effective_metrics(perfor_[p], neutralize) for p in periods]

    ls = [e['long_short_return'] for e in eff]
    axes[0].bar(range(len(periods)), ls, color='skyblue', alpha=0.8)
    axes[0].set_xticks(range(len(periods)))
    axes[0].set_xticklabels([f'{p}{period_unit(REBALANCE)}' for p in periods])
    axes[0].set_title('多空组合收益', fontweight='bold')
    axes[0].set_ylabel('收益')
    for i, v in enumerate(ls):
        axes[0].text(i, v, f'{v:.3f}', ha='center', va='bottom')

    ics = [e['ic'] for e in eff]
    axes[1].bar(range(len(periods)), ics, color='lightcoral', alpha=0.8)
    axes[1].set_xticks(range(len(periods)))
    axes[1].set_xticklabels([f'{p}{period_unit(REBALANCE)}' for p in periods])
    axes[1].set_title('信息系数(IC)', fontweight='bold')
    axes[1].set_ylabel('IC')
    for i, v in enumerate(ics):
        axes[1].text(i, v, f'{v:.3f}', ha='center', va='bottom')

    if periods:
        qr = eff[0]['quantile_returns']
        axes[2].plot(qr.index, qr.values, marker='o', linewidth=2, markersize=8, color='blue')
        axes[2].set_title(f'{periods[0]}{period_unit(REBALANCE)}期分位数收益', fontweight='bold')
        axes[2].set_xlabel('分位数')
        axes[2].set_ylabel('平均收益')
        axes[2].grid(True, alpha=0.6)
        for i, v in enumerate(qr.values):
            axes[2].text(qr.index[i], v, f'{v:.3f}', ha='center', va='bottom')

    if periods and len(eff[0]['ic_series']):
        s = eff[0]['ic_series']
        axes[3].plot(s.index, s.values, linewidth=1, color='purple', alpha=0.7)
        axes[3].axhline(y=s.mean(), color='red', linestyle='--', label=f'均值: {s.mean():.3f}')
        axes[3].set_title(f'{periods[0]}{period_unit(REBALANCE)}期IC时间序列', fontweight='bold')
        axes[3].set_xlabel('日期')
        axes[3].set_ylabel('IC')
        axes[3].legend()
        axes[3].grid(True, alpha=0.3)

    fig.tight_layout()
    _finish_fig(fig, 'factor_performance.png')


def plot_ic_distribution(perfor_, neutralize=None):
    """IC 分布直方图 + 累计 IC 曲线（取第一个持有期为代表期）。"""
    periods = sorted(perfor_.keys())
    if not periods:
        return
    p0 = periods[0]
    perf = perfor_[p0]
    raw = perf['ic_series']
    neut = perf['neutralized']['ic_series'] if neutralize and 'neutralized' in perf else None

    fig, axes = plt.subplots(1, 2, figsize=(15, 5.5))
    # 左：直方图
    ax = axes[0]
    if len(raw):
        ax.hist(raw, bins=40, alpha=0.5, color='#4C72B0',
                label=f'原始  IC={perf["ic"]:.4f}  ICIR={perf["ic_ir"]:.3f}')
    if neut is not None and len(neut):
        ax.hist(neut, bins=40, alpha=0.5, color='#DD8452',
                label=f'中性化  IC={perf["neutralized"]["ic"]:.4f}  ICIR={perf["neutralized"]["ic_ir"]:.3f}')
    ax.axvline(0, color='gray', linestyle='--', linewidth=1)
    ax.set_title(f'{p0}{period_unit(REBALANCE)}期 IC 分布', fontsize=14, fontweight='bold')
    ax.set_xlabel('每日 IC'); ax.set_ylabel('频次')
    ax.legend(fontsize=10); ax.grid(True, alpha=0.3)
    # 右：累计 IC（cumsum，考察信号单调性）
    ax = axes[1]
    if len(raw):
        ax.plot(raw.index, raw.cumsum(), color='#4C72B0', label='原始', linewidth=1.5)
    if neut is not None and len(neut):
        ax.plot(neut.index, neut.cumsum(), color='#DD8452', linestyle='--', label='中性化', linewidth=1.5)
    ax.axhline(0, color='gray', linestyle='--', linewidth=1)
    ax.set_title(f'{p0}{period_unit(REBALANCE)}期 累计 IC（cumsum）', fontsize=14, fontweight='bold')
    ax.set_xlabel('日期'); ax.set_ylabel('累计 IC')
    ax.legend(fontsize=10); ax.grid(True, alpha=0.3)
    fig.tight_layout()
    _finish_fig(fig, 'ic_distribution.png')


def plot_fm_regression(perfor_, neutralize=None):
    """FM 逐日截面斜率时序 + 斜率分布直方图（取第一个持有期为代表期）。"""
    periods = sorted(perfor_.keys())
    if not periods:
        return
    p0 = periods[0]
    perf = perfor_[p0]
    raw_fm = perf['metrics']['fm']
    neut_fm = perf['neutralized']['fm'] if neutralize and 'neutralized' in perf else None

    fig, axes = plt.subplots(1, 2, figsize=(15, 5.5))
    # 左：斜率时序
    ax = axes[0]
    if len(raw_fm['slope_series']):
        ax.plot(raw_fm['slope_series'].index, raw_fm['slope_series'].values,
                color='#4C72B0', label='原始', linewidth=1.2)
        ax.axhline(raw_fm['mean_slope'], color='#4C72B0', linestyle='--', alpha=0.7,
                   label=f'原始均值 {raw_fm["mean_slope"]:.5f}')
    if neut_fm is not None and len(neut_fm['slope_series']):
        ax.plot(neut_fm['slope_series'].index, neut_fm['slope_series'].values,
                color='#DD8452', linestyle='--', linewidth=1.2, label='中性化')
        ax.axhline(neut_fm['mean_slope'], color='#DD8452', linestyle=':', alpha=0.7,
                   label=f'中性化均值 {neut_fm["mean_slope"]:.5f}')
    ax.axhline(0, color='gray', linewidth=1)
    t_nw = neut_fm['t_nw'] if neut_fm is not None else raw_fm['t_nw']
    ax.set_title(f'{p0}{period_unit(REBALANCE)}期 FM 逐日截面斜率\n(均值BP={raw_fm["slope_bp"]:.1f} · FM_t_NW={t_nw:.2f})',
                 fontsize=14, fontweight='bold')
    ax.set_xlabel('调仓日'); ax.set_ylabel('截面回归斜率')
    ax.legend(fontsize=9); ax.grid(True, alpha=0.3)
    # 右：斜率直方图
    ax = axes[1]
    if len(raw_fm['slope_series']):
        ax.hist(raw_fm['slope_series'], bins=40, alpha=0.5, color='#4C72B0', label='原始')
    if neut_fm is not None and len(neut_fm['slope_series']):
        ax.hist(neut_fm['slope_series'], bins=40, alpha=0.5, color='#DD8452', label='中性化')
    ax.axvline(0, color='gray', linestyle='--', linewidth=1)
    ax.set_title(f'{p0}{period_unit(REBALANCE)}期 FM 斜率分布', fontsize=14, fontweight='bold')
    ax.set_xlabel('每日斜率'); ax.set_ylabel('频次')
    ax.legend(fontsize=10); ax.grid(True, alpha=0.3)
    fig.tight_layout()
    _finish_fig(fig, 'fm_regression.png')


def plot_t_value_compare(perfor_, neutralize=None):
    """naive vs Newey-West t 值对比（IC / FM 斜率 / 多空组合），直观展示自相关修正的力度。"""
    periods = sorted(perfor_.keys())
    if not periods:
        return
    eff = [effective_metrics(perfor_[p], neutralize) for p in periods]

    triplets = [
        ('IC 的 t 值', [m['ic_t_naive'] for m in eff], [m['ic_t_nw'] for m in eff]),
        ('FM 斜率的 t 值', [m['fm']['t_naive'] for m in eff], [m['fm']['t_nw'] for m in eff]),
        ('多空组合的 t 值', [m['ls_t_naive'] for m in eff], [m['ls_t_nw'] for m in eff]),
    ]
    x = np.arange(len(periods))
    width = 0.38
    fig, axes = plt.subplots(1, 3, figsize=(18, 5.5))
    for ax, (title, naive, nw) in zip(axes, triplets):
        ax.bar(x - width / 2, naive, width, color='#4C72B0', label='naive t')
        ax.bar(x + width / 2, nw, width, color='#DD8452', label='Newey-West t')
        ax.axhline(1.96, color='green', linestyle='--', linewidth=1, alpha=0.7, label='|t|=1.96')
        ax.set_xticks(x)
        ax.set_xticklabels([f'{p}{period_unit(REBALANCE)}' for p in periods])
        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.legend(fontsize=9); ax.grid(True, alpha=0.3, axis='y')
        for xi, (a, b) in enumerate(zip(naive, nw)):
            ax.text(xi - width / 2, a, f'{a:.1f}', ha='center', va='bottom', fontsize=8)
            ax.text(xi + width / 2, b, f'{b:.1f}', ha='center', va='bottom', fontsize=8)
    fig.suptitle('naive vs Newey-West t 值（修正自相关后 t 值下降 = 扣减虚高）',
                 fontsize=15, fontweight='bold')
    fig.tight_layout()
    _finish_fig(fig, 't_value_compare.png')


def plot_neutralization_compare(perfor_):
    """中性化对比仪表盘（2×2）：分位收益 / 多空净值 / 市值诊断散点 / IC 对比。仅中性化开启时调用。"""
    periods = sorted(perfor_.keys())
    if not periods:
        return
    p0 = periods[0]
    perf = perfor_[p0]
    if 'neutralized' not in perf:
        return
    colors = _quantile_colors()

    fig, axes = plt.subplots(2, 2, figsize=(16, 11))
    axes = axes.flatten()

    # 左上：分位数累计收益对比（实线=原始 / 虚线=中性化）
    ax = axes[0]
    raw_cum = _cum_quantiles(perf['metrics'])
    neut_cum = _cum_quantiles(perf['neutralized'])
    for qq in sorted(raw_cum.columns):
        ax.plot(raw_cum.index, raw_cum[qq], color=colors.get(qq, 'gray'), linewidth=1.5, alpha=0.9)
    for qq in sorted(neut_cum.columns):
        ax.plot(neut_cum.index, neut_cum[qq], linestyle='--', linewidth=1.0,
                color=colors.get(qq, 'gray'), alpha=0.5)
    ax.set_title(f'{p0}{period_unit(REBALANCE)}期 分位数累计收益（实线=原始 / 虚线=中性化）',
                 fontsize=13, fontweight='bold')
    ax.set_xlabel('日期'); ax.set_ylabel('累计净值'); ax.grid(True, alpha=0.3)

    # 右上：多空组合累计净值对比
    ax = axes[1]
    raw_ls = perf['metrics']['ls_series']
    neut_ls = perf['neutralized']['ls_series']
    if len(raw_ls):
        ax.plot(raw_ls.index, (1 + raw_ls).cumprod(), color='#4C72B0', label='原始', linewidth=1.5)
    if len(neut_ls):
        ax.plot(neut_ls.index, (1 + neut_ls).cumprod(), color='#DD8452', linestyle='--',
                label='中性化', linewidth=1.5)
    ax.axhline(1, color='gray', linewidth=1)
    ax.set_title(f'{p0}{period_unit(REBALANCE)}期 多空组合累计净值（Q10−Q1）', fontsize=13, fontweight='bold')
    ax.set_xlabel('日期'); ax.set_ylabel('累计净值')
    ax.legend(fontsize=10); ax.grid(True, alpha=0.3)

    # 左下：因子 vs log(市值) 诊断散点（取样本数最多的代表日，按行业着色）
    ax = axes[2]
    d = perf['data'].dropna(subset=['factor', 'mkt_cap'])
    if len(d):
        day = d['trade_date'].value_counts().idxmax()
        sub = d[d['trade_date'] == day]
        x = np.log(sub['mkt_cap'].to_numpy(dtype=float))
        y = sub['factor'].to_numpy(dtype=float)
        industries = sorted(sub['industry'].dropna().unique())
        cmap = plt.get_cmap('tab20')
        for i, ind in enumerate(industries):
            m = (sub['industry'] == ind).to_numpy(dtype=bool)
            ax.scatter(x[m], y[m], s=14, alpha=0.6, color=cmap(i % 20),
                       label=ind if i < 12 else None)
        coef = np.polyfit(x, y, 1)
        xs = np.sort(x)
        ax.plot(xs, np.polyval(coef, xs), color='red', linewidth=2,
                label=f'拟合斜率 {coef[0]:.3f}')
        mcc = perf.get('mkt_cap_corr', float('nan'))
        ax.set_title(f'{pd.Timestamp(day).date()} 因子 vs log(市值)（日均秩相关={mcc:.3f}）',
                     fontsize=13, fontweight='bold')
        ax.set_xlabel('log(总市值)'); ax.set_ylabel('因子值')
        ax.legend(fontsize=8, loc='lower left', ncol=2); ax.grid(True, alpha=0.3)

    # 右下：各持有期 原始 vs 中性化 IC 对比
    ax = axes[3]
    raw_ics = [perfor_[p]['metrics']['ic'] for p in periods]
    neut_ics = [perfor_[p]['neutralized']['ic'] for p in periods]
    x = np.arange(len(periods))
    width = 0.38
    ax.bar(x - width / 2, raw_ics, width, color='#4C72B0', label='原始 IC')
    ax.bar(x + width / 2, neut_ics, width, color='#DD8452', label='中性化 IC')
    ax.axhline(0, color='gray', linewidth=1)
    ax.set_xticks(x); ax.set_xticklabels([f'{p}{period_unit(REBALANCE)}' for p in periods])
    ax.set_title('各持有期 原始 vs 中性化 IC', fontsize=13, fontweight='bold')
    ax.legend(fontsize=10); ax.grid(True, alpha=0.3, axis='y')
    for xi, (a, b) in enumerate(zip(raw_ics, neut_ics)):
        ax.text(xi - width / 2, a, f'{a:.3f}', ha='center', va='bottom', fontsize=8)
        ax.text(xi + width / 2, b, f'{b:.3f}', ha='center', va='bottom', fontsize=8)

    fig.suptitle('市值 + 行业中性化效果对比', fontsize=16, fontweight='bold')
    fig.tight_layout()
    _finish_fig(fig, 'neutralization_compare.png')


###############################################################################
#### 主流程 ####
###############################################################################

def main():
    print('=' * 60)
    print(f'单因子回测（改进版） | 因子={FACTOR} | 调仓={REBALANCE} | '
          f'持有期={PERIODS} | 中性化={NEUTRALIZE}')
    print('=' * 60)

    # 读取数据并合并
    price_data = pd.read_parquet(DATA_PRICE)
    fct_df = pd.read_parquet(DATA_FCT)
    factor_names = list(fct_df.columns)
    price_data = pd.concat([price_data, fct_df], axis=1, join='inner').sort_index()
    print(f'合并后数据: {len(price_data):,} 行 × {price_data.shape[1]} 列')

    # 预处理：先过滤样本池，再每日截面 MAD + Z-score（修复问题一）
    price_data = preprocess_data(price_data)
    price_data = preprocess_factors(price_data, factor_names)
    print(f'预处理后: {len(price_data):,} 行（剔除 ST / 上市不足一年，每日截面去极值+标准化）')

    if FACTOR not in price_data.columns:
        raise ValueError(f'数据中没有因子列 {FACTOR}，可选: {factor_names}')

    factor_series = price_data[FACTOR]

    # 回测（修复问题二：日历化调仓）
    results, factor_ctx = factor_analysis(factor_series, price_data, periods=PERIODS,
                                          quantiles=QUANTILES, rebalance=REBALANCE)

    # 表现分析（修复问题三、四：FM 回归 + NW 检验 + 风格中性化）
    performance = analyze_factor_performance(
        results, neutralize=NEUTRALIZE, quantiles=QUANTILES,
        price_aligned=factor_ctx['price_aligned'], reb_dates=factor_ctx['reb_dates'],
        rebalance=REBALANCE)

    summary_df = generate_summary_statistics(performance, NEUTRALIZE, REBALANCE, PERIODS,
                                             output_path=OUTPUT_PATH)

    print('\n=== 详细统计结果汇总 ===')
    print(summary_df.to_string(index=False, float_format='%.4f'))

    # 可视化前清空 figs/：保证目录里只有本次运行的完整图表，避免旧图残留造成混淆
    if os.path.isdir(FIGS_DIR):
        for _f in os.listdir(FIGS_DIR):
            if _f.endswith('.png'):
                os.remove(os.path.join(FIGS_DIR, _f))
        print(f'已清空旧图表目录 {FIGS_DIR}/')

    # 可视化
    plot_factor_performance(performance, NEUTRALIZE)
    plot_quantile_returns_separate(performance, NEUTRALIZE)
    # 统计检验与中性化可视化（新增）
    plot_ic_distribution(performance, NEUTRALIZE)
    plot_fm_regression(performance, NEUTRALIZE)
    plot_t_value_compare(performance, NEUTRALIZE)
    if NEUTRALIZE:
        plot_neutralization_compare(performance)

    print(f'\n全部图表已保存至: {FIGS_DIR}/（分层曲线数量 = 持有期数 {len(PERIODS)}）')


if __name__ == '__main__':
    main()
