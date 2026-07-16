# -*- coding: utf-8 -*-
"""
Created on Tue Dec  2 13:40:27 2025

@author: andrew
"""
#前言：
'''
模型的任务是：用现在的 X，去预测未来的 Y。
回测的任务是：验证历史上 X 和 Y 之间是否存在稳定的统计关系。
'''
###############################################################################
#### import ####

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from scipy import stats
import warnings
warnings.filterwarnings('ignore')

plt.rcParams['font.sans-serif'] = ['Arial Unicode MS'] #Arial Unicode MS
plt.rcParams['axes.unicode_minus'] = False


###############################################################################
#### func ####

'''
  当前实现是全局 MAD 缩尾——中位数和 MAD 都是对全部时间段的所有股票一起计算的，得到一个全局上下界：

  median = dt.quantile(0.5)           # 全样本中位数（跨时间+股票）
  new_median = (abs(dt - median)).quantile(0.5)  # 全样本 MAD

  而量化回测中更常见的做法是每日截面 MAD 缩尾（在每个交易日内，对该日所有股票的因子值做缩尾）。
  两种方法的差异在于：全局缩尾使用的是静态阈值，截面缩尾使用的是动态阈值。如果因子本身存在明显的 时间趋势（例如某些因子在市场不同阶段分布不同），全局缩尾可能在某些时段过宽或过窄。
'''
def extreme_MAD(dt, n=5.2):
    median = dt.quantile(0.5) #quantile(q) 用于计算数据的分位数（四分位数 / 百分位数），可以理解为：给定一个 0~1 之间的比例q，返回把数据集从小到大排序后，排在前q比例位置上的数值
    new_median = (abs((dt - median)).quantile(0.5)) #计算每个值到该股票中位数的绝对偏差，再取中位数，得到该股票的 MAD（绝对中位差）
    dt_up = median + n*new_median #上界
    dt_down = median - n*new_median #下界
    return dt.clip(dt_down, dt_up, axis=1) #将数据限制在上下界之间

'''
pandas.DataFrame.clip(lower, upper, axis) 是截断缩尾（Winsorize）函数：
把数据里小于下界 lower的数值，强制替换成 lower；
把大于上界 upper的数值，强制替换成 upper；
落在上下界中间的数据保持原值不变。
'''
def standardize_z(dt):
    mean = dt.mean() #求均值
    std = dt.std() #求标准差
    return (dt - mean)/std #标准化

def preprocess_data(data):
    df = data.copy() #复制数据，深拷贝一份数据，避免修改原DataFrame
    mask = (~df['is_st']) & (df['trade_days'] >= 365) #~ pandas 里的按位取反（取非），等价于 not，&且（同时满足两个条件），pandas 多条件筛选必须用&不能用and，即剔除st股票和上市不足一年的股票
    df = df[mask].copy() #只取出 mask 为 True 的行，完成样本池初次过滤
    df.drop(['is_st','trade_days'], axis=1, inplace=True) #前面已经用这两列完成筛选，后续不再需要这两个标记字段，直接从数据表中删除这两列，精简数据维度
    return df
'''
输入数据：

......results = factor_analysis(factor_df, price_data, periods=(1, 5, 10, 20), quantiles=10)
......price_data = pd.concat([price_data,fct_df],axis=1,join='inner')

......预处理

......factor_df = price_data['fct_2']

'''
def factor_analysis(factor_series, price_df, periods=(1, 5, 10,20), quantiles=10):
    results = {} #创建一个空字典，用于存储每个持有期的因子分析结果，先将其暂存在内存当中
    price_aligned = pd.pivot_table(price_df['close_adj'].reset_index(),index='trade_date', columns='code', values='close_adj') #将价格数据透视为宽表，索引为交易日期，列为股票代码，值为对应的收盘价（复权后），方便后续计算未来收益率
        # =========================================================================
        # 【核心时间轴偏移详解】—— 解答“为何同一日期却代表未来收益”
        # 
        # 假设 price_aligned 当前索引行对应的日期为 T 日。
        # 
        # 1. current_prices (shift(-1))：
        #    -> 取 T+1 日的收盘价作为“买入价”。
        #    -> 目的：T 日收盘时因子值已知，但已无法按 T 日收盘价交易，
        #       因此用次日（T+1）价格模拟开盘买入，避免前视偏差。
        # 
        # 2. future_prices (shift(-period-1))：
        #    -> 取 T+period+1 日的收盘价作为“卖出价”。
        #    -> 例：若 period=5，则取 T+6 日价格，表示持有 5 个交易日。
        # 
        # 3. future_returns = (卖出价 / 买入价) - 1：
        #    -> 计算出的收益率实际对应时间区间为 [T+1, T+period+1]。
        #    -> 该数值会被存入索引为 T 日的行中。
        # 
        # 【重点】后续 merge 合并时：
        #    factor_df 的 trade_date = T 日（因子值已知日）
        #    returns_series 的 index = T 日（虽然内部数值来自未来，但标签贴在了 T 日）
        #    合并后表格看着是同一日期，实则是将“T 日因子值”与“T 日开始的未来收益”强行配对。
        # 
        # 结论：这形成了严格的“当前截面特征 -> 未来区间收益”映射。
        #      后续按 trade_date 分组计算 IC 时，衡量的是 T 日因子排序对 T 日后收益的预测能力，
        #      完全正确，不存在用未来数据预测未来的逻辑错误。
        # =========================================================================
    for period in periods: #遍历每个持有期，计算未来收益率，并将因子值和未来收益率合并为一个DataFrame，存储在results字典中
        future_prices = price_aligned.shift(-period-1) #取T+period+1日的价格作为卖出价
        current_prices = price_aligned.shift(-1)  # 取T+1日的价格作为买入价（假设T日收盘知道因子值，T+1日开盘买入，但这里用T+1收盘价代替，略有简化）
        future_returns = (future_prices / current_prices - 1) #持有期收益 = (卖出价 / 买入价) - 1，即从T+1日到T+period+1日的收益（持有period天），从 T+1 日到 T+period+1 日。因为 shift(-period-1) 取的是第 t+period+1 行的价格
        factor_df = factor_series.reset_index() #将因子序列（多索引Series）重置为普通DataFrame，并重命名列
        factor_df.columns = ['trade_date', 'code', 'factor']  #将因子序列（多索引Series）重置为普通DataFrame，并重命名列
        
        future_returns = future_returns.iloc[::period].dropna(axis=0,how='all') #iloc[::period] —— 每隔 period 行取一个日期，目的是避免样本重叠（例如持有5天，只取第1、6、11...天的数据）。这会大幅减少样本量，但使收益序列独立同分布，便于统计ICIR。
        #只保留T、T+period、T+2*period...的收益率，避免样本重叠，减少自相关性。dropna(axis=0,how='all') —— 删除所有股票收益率都为 NaN 的日期行，确保后续计算的有效性

        returns_series = future_returns.stack() #stack() 将宽表变回长表（日期、股票、收益），然后按日期+股票合并因子值和未来收益
        returns_series.name = f'return_{period}d' #给收益序列命名，即列名为 return_1d、return_5d、return_10d 等
        merged_df = pd.merge(factor_df, returns_series, 
                           left_on=['trade_date', 'code'], 
                           right_index=True) #按日期和股票代码合并因子值和未来收益，得到一个新的DataFrame，包含 trade_date、code、factor、return_{period}d 四列
        
        merged_df = merged_df.dropna(subset=['factor', f'return_{period}d']) #删除因子值或未来收益为 NaN 的行，确保后续计算的有效性
        merged_df = merged_df.sort_values('trade_date') #按日期排序
        
        if len(merged_df) > 0:
            merged_df['quantile'] = merged_df.groupby('trade_date')['factor'].transform(
                lambda x: pd.qcut(x, quantiles, labels=False, duplicates='drop') + 1
            ) #按日期对因子值进行分位数划分，使用 pd.qcut 将因子值分为 quantiles 个分位数，并将分位数编号从 1 开始（即 Q1、Q2、...、Q10），如果某一天的因子值不足以划分 quantiles 个分位数，则自动去掉重复的分位数标签
            results[period] = merged_df #将每个持有期的合并数据存储在 results 字典中，键为持有期，值为对应的 DataFrame，包含 trade_date、code、factor、return_{period}d 和 quantile 列
  
    return results

'''
假设某一天（2026-01-05）只有 4 只股票 A、B、C、D，我们设置 quantiles=4（切成 4 组，方便演示）

原始数据（该日抽屉里的内容）：

code	factor
A	    1.2
B	    -0.5
C	    0.8
D	    1.5

执行 pd.qcut 的过程：

排序：-0.5（B），0.8（C），1.2（A），1.5（D）

等频切分成 4 份（每组 1 只股票）：

第 1 组（最小）：B（-0.5）  → 编号 0

第 2 组：C（0.8）           → 编号 1

第 3 组：A（1.2）           → 编号 2

第 4 组（最大）：D（1.5）   → 编号 3

labels=False 返回：[0, 1, 2, 3]

+1 之后：变成 [1, 2, 3, 4]

transform 自动对齐原顺序：因为原表顺序是 A, B, C, D，所以把编号按原索引贴回去：

A 得到 3

B 得到 1

C 得到 2

D 得到 4

最终该日生成的 quantile 列结果：

code	factor	quantile
A	    1.2	    3
B	    -0.5	1
C	    0.8	    2
D	    1.5	    4
'''

# results:

'''
trade_date (T日)	code	factor(T日因子值)	       return_5d (实际交易区间)	         quantile
——————————————————————————————————————————————————————————————————————————————————————————————
2026-01-05	        A	        1.20	             +4.95%(买入: 1/06, 卖出: 1/13)	      10
2026-01-05	        B	        -0.50	             -5.94%(买入: 1/06, 卖出: 1/13)	       1
2026-01-06	        C	        0.80	             +2.10%(买入: 1/07, 卖出: 1/14)	       7
2026-01-06	        D	        1.50	             +1.50%(买入: 1/07, 卖出: 1/14)	       9
——————————————————————————————————————————————————————————————————————————————————————————————
'''


def analyze_factor_performance(res_):
    perform_ = {} #创建一个空字典，用于存储每个持有期的因子表现分析结果，先将其暂存在内存当中
    
    for period, df in res_.items(): #遍历每个持有期的合并数据 DataFrame，计算分位数收益率、多空组合收益率、IC均值、ICIR和IC时间序列，并将结果存储在 perform_ 字典中
        quantile_returns = df.groupby(['trade_date','quantile'])[f'return_{period}d'].mean() #按日期和分位数计算平均收益，得到一个多索引Series，索引为 (trade_date, quantile)，值为 return_{period}d 的平均值
        quantile_returns = quantile_returns.reset_index() #将多索引Series重置为普通DataFrame，列名为 trade_date、quantile、return_{period}d
        quantile_returns = pd.pivot_table(quantile_returns,index='trade_date',columns='quantile',values=f'return_{period}d') #将DataFrame透视为宽表，索引为 trade_date，列为 quantile，值为 return_{period}d 的平均值
        quantile_returns = quantile_returns.add(1).cumprod().sub(1).iloc[-1] #计算每个分位数的累计收益率，先加1再累乘再减1，得到每个分位数的总收益率
        #.cumprod()：沿时间轴（从上到下）累乘，得到每天的累计净值曲线


        long_short_return = quantile_returns.iloc[-1] - quantile_returns.iloc[0] #计算多空组合收益率，即最高分位数的累计收益率减去最低分位数的累计收益率
        #多空收益 = 做多 Top 组 + 做空 Bottom 组 的理论收益。如果这个值为正，说明因子具有正向选股能力（高因子值未来收益更高）
        '''
        多空组合的策略逻辑就是 买入 Top 组（因子值最大），卖出 Bottom 组（因子值最小）。

        代码里只做了 Q10 - Q1 的减法，是因为在量化回测的数学建模中，这个减法精确模拟了“等权重多空对冲组合”的每日净值变化。我们算一笔账就懂了：

        假设总资金为 2 块钱（1 块做多，1 块做空）：

        做多 Top 组：投入 1 块，如果 Top 组涨了 10%，这边赚了 0.1 块。

        做空 Bottom 组：投入 1 块（借股票卖掉），如果 Bottom 组涨了 8%，因为你是做空，这边亏了 0.08 块。

        整个组合的总盈亏 = 0.1 - 0.08 = 0.02 块，也就是 10% - 8% = 2%。

        而反之如果做多组跌了做空组涨了，或者做多组涨了做空组跌了，以及其它情况，最终的盈亏都是 Q10 - Q1 的差值。

        所以，Q10 - Q1 这个差值，在数学上直接等价于“等权重多空组合”的净收益率。正因为计算这么简洁，量化界才直接用这个差值代表多空组合的收益表现
        '''

        ic_series = df.groupby('trade_date').apply(
            lambda x: stats.spearmanr(x['factor'], x[f'return_{period}d']).correlation 
            if len(x) > 5 else np.nan
        ).dropna() #按日期计算因子值和未来收益的Spearman秩相关系数（IC），如果某一天的样本量小于等于5，则返回NaN，最后去掉NaN值，得到一个按日期索引的IC时间序列
        
        if len(ic_series) > 0:  #计算IC的均值和ICIR（信息比率），ICIR = IC均值 / IC标准差，如果IC标准差为0，则ICIR为0
            ic = ic_series.mean()
            ic_ir = ic_series.mean() / ic_series.std() if ic_series.std() > 0 else 0 
            
            perform_[period] = {
                'quantile_returns': quantile_returns, 
                'long_short_return': long_short_return,
                'ic': ic,
                'ic_ir': ic_ir,
                'ic_series': ic_series,
                'data': df
            } #存储每个持有期的分析结果，包括分位数收益率、多空组合收益率、IC均值、ICIR、IC时间序列和原始数据
    
    return perform_ #返回绩效数据字典

'''
1. 画收益曲线分两种，容易混淆：
- 画每日单日收益率：直接用当天原始收益率，不累乘，日期与收益率同行对应绘图；
- 画账户净值/累计收益曲线（常用收益曲线）：必须用`1+当日收益率`逐行累乘。

2. 最关键疑问：累乘出来的净值该匹配哪一天日期？
计算得出的当期净值，就和产生这笔收益率的当天日期放在同一行、一一对齐。
初始基准净值1，是期初起跑线，不绑定任何交易日；
每一行日期+本行收益率，计算出本行净值，本行日期绑定本行净值，绘图不会时间错位。

3. 简单操作口诀
期初先设净值=1；
下一行净值=上一行净值×(1+本行当日收益率)；
净值与本行日期配对作图。
'''

def calculate_portfolio_metrics(returns_series, period_days=1):  
    if len(returns_series) == 0:
        return {}
    
    cumulative_curve = (1 + returns_series).cumprod() #计算累计收益曲线，即每一天的累计收益率 = (1 + 当天收益率) 的累乘积
    cumulative_return = cumulative_curve.iloc[-1] - 1 if len(cumulative_curve) > 0 else 0 #计算总累计收益率，即最后一天的累计收益率减去1，如果累计曲线为空，则累计收益率为0
    
    if len(returns_series) > 0: #计算年化收益率，假设一年有252个交易日，年化收益率 = (1 + 累计收益率) ^ (252 / 总天数) - 1
        total_days = len(returns_series) * period_days #把“压缩后的样本数量”还原成“真实经历的自然交易日天数”，确保年化收益率和年化波动率的计算符合真实的时间尺度，不会因为非重叠采样而失真
        annual_return = (1 + cumulative_return) ** (252 / total_days) - 1
    else:
        annual_return = 0 #如果收益序列为空，则年化收益率为0
    
    running_max = cumulative_curve.expanding().max() #计算累计收益曲线的滚动最大值，即每一天的累计收益率的历史最高点，rolling(window=3)固定大小（比如只看最近3天），计算移动平均线（MA5、MA10）；expanding()从第1天到当天的所有数据（越往后窗口越大），计算历史最高点、累计总和、累计均值
    drawdown = (cumulative_curve - running_max) / running_max #计算回撤率，即每一天的累计收益率与历史最高点的差值除以历史最高点，得到一个负数序列，表示每一天的回撤幅度
    max_drawdown = abs(drawdown.min()) #计算最大回撤，即回撤率的最小值的绝对值，表示投资组合在持有期内可能遭受的最大损失比例
    
    if len(returns_series) > 1: #计算年化波动率，年化波动率 = 日收益率标准差 * sqrt(252 / 总天数)，如果收益序列长度小于等于1，则年化波动率为0
        annual_volatility = returns_series.std() * np.sqrt(252 / period_days) #计算年化波动率
    else:
        annual_volatility = 0 #如果收益序列长度小于等于1，则年化波动率为0
    
    sharpe_ratio = annual_return / annual_volatility if annual_volatility > 0 else 0 #计算夏普比率，夏普比率 = 年化收益率 / 年化波动率，如果年化波动率为0，则夏普比率为0
    
    downside_returns = returns_series[returns_series < 0] #计算下行波动率，即只考虑负收益的收益序列
    if len(downside_returns) > 1:
        downside_volatility = downside_returns.std() * np.sqrt(252 / period_days) #计算下行波动率，年化下行波动率 = 负收益的标准差 * sqrt(252 / 总天数)，如果负收益序列长度小于等于1，则下行波动率为0
    else:
        downside_volatility = 0 #如果负收益序列长度小于等于1，则下行波动率为0
    sortino_ratio = annual_return / downside_volatility if downside_volatility > 0 else 0 #计算索提诺比率，索提诺比率 = 年化收益率 / 年化下行波动率，如果年化下行波动率为0，则索提诺比率为0
    
    calmar_ratio = annual_return / max_drawdown if max_drawdown > 0 else 0 #计算卡玛比率，卡玛比率 = 年化收益率 / 最大回撤，如果最大回撤为0，则卡玛比率为0
    
    return {
        '累计收益率': cumulative_return,
        '年化收益率': annual_return,
        '最大回撤': -max_drawdown,
        '夏普比率': sharpe_ratio,
        '索提诺比率': sortino_ratio,
        '卡玛比率': calmar_ratio,
       
    }

def determine_factor_direction(perfo_): 
    direction_info = {} #创建一个空字典，用于存储每个持有期的因子方向信息
    '''
    1. 正向因子（Positive Factor）
    定义：因子值越大，未来收益率越高。

    特征：Q10（高因子组） - Q1（低因子组） > 0

    操作：应该买入高因子值的股票（Top组）。

    现实例子：动量因子（过去涨得多的，未来继续涨）；盈利能力因子（ROE高的公司，股价表现好）。

    2. 负向因子（Negative Factor）
    定义：因子值越小，未来收益率越高（即因子值与未来收益成反比）。

    特征：Q10（高因子组） - Q1（低因子组） < 0

    操作：应该买入低因子值的股票（Bottom组），做空高因子值的股票。

    现实例子：市盈率（PE）（PE低的股票更便宜，未来上涨空间大，即“价值因子”）；换手率（换手率越低，未来表现通常越好）
    '''
    for period, perf in perfo_.items(): #遍历每个持有期的绩效数据字典，获取多空组合收益率，并根据其正负值判断因子方向
        long_short_return = perf['long_short_return'] #获取多空组合收益率，即最高分位数的累计收益率减去最低分位数的累计收益率
        if long_short_return > 0:
            direction = '正向因子'
            target_group = 'Top组(Q10)'
            target_quantile = 10
        else:
            direction = '负向因子'
            target_group = 'Bottom组(Q1)'
            target_quantile = 1
        
        direction_info[period] = {
            'direction': direction,
            'target_group': target_group,
            'target_quantile': target_quantile,
            'long_short_return': long_short_return
        } #将每个持有期的因子方向信息存储在字典中，包括因子方向、目标分组、目标分位数和多空组合收益率
    
    return direction_info #返回因子方向信息字典

def generate_summary_statistics(perform_, output_path='因子回测统计结果.xlsx'): 
    direction_info = determine_factor_direction(perform_)    
    summary_data = [] #创建一个空列表，用于存储每个持有期的汇总统计数据
    
    for period in perform_.keys():
        df = perform_[period]['data'] #获取每个持有期的原始数据DataFrame，包括 trade_date、code、factor、return_{period}d 和 quantile 列
        direction_data = direction_info[period] #获取每个持有期的因子方向信息，包括因子方向、目标分组、目标分位数和多空组合收益率        
        daily_portfolio_returns = df.groupby(['trade_date', 'quantile'])[f'return_{period}d'].mean().reset_index() #按日期和分位数计算平均收益，得到一个DataFrame，包括 trade_date、quantile 和 return_{period}d 列
        returns_pivot = daily_portfolio_returns.pivot(index='trade_date', columns='quantile', values=f'return_{period}d') #将DataFrame透视为宽表，索引为 trade_date，列为 quantile，值为 return_{period}d 的平均值
        returns_pivot = returns_pivot.sort_index()#按日期排序，确保时间序列的正确性
        target_quantile = direction_data['target_quantile']#获取目标分位数，即最高分位数（Q10）或最低分位数（Q1），用于计算对应的收益率和指标
        
        if target_quantile in returns_pivot.columns:
            target_returns = returns_pivot[target_quantile].dropna() #获取目标分位数的收益率序列，并去掉缺失值，得到一个按日期索引的Series
        else:
            print(f"警告: 持有期{period}天没有找到分位数{target_quantile}") #如果目标分位数不存在于透视表的列中，打印警告信息，并跳过该持有期的统计计算
            continue
        
        metrics = calculate_portfolio_metrics(target_returns, period) #计算目标分位数的投资组合指标，包括累计收益率、年化收益率、最大回撤、夏普比率、索提诺比率和卡玛比率
        
        period_summary = {
            '持有期(天)': period,
            '因子方向': direction_data['direction'],
            'IC': perform_[period]['ic'],
            'ICIR': perform_[period]['ic_ir'],
            '累计收益率': metrics['累计收益率'],
            '年化收益率': metrics['年化收益率'],
            '最大回撤': metrics['最大回撤'],
            '夏普比率': metrics['夏普比率'],
        }
        
        summary_data.append(period_summary) #将每个持有期的汇总统计数据添加到列表中
    
    if not summary_data:
        return pd.DataFrame() #如果没有任何汇总数据，则返回一个空的DataFrame，避免后续操作出错
        
    summary_df = pd.DataFrame(summary_data) #将汇总统计数据列表转换为DataFrame，列名为持有期(天)、因子方向、IC、ICIR、累计收益率、年化收益率、最大回撤和夏普比率
    
    try:
        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            summary_df.to_excel(writer, sheet_name='汇总统计', index=False) #将汇总统计数据写入Excel文件的“汇总统计”工作表中，不包含索引列
            
            quantile_returns_data = []
            for period in perform_.keys(): #遍历每个持有期，获取对应的分位数收益率数据，并将其存储为字典列表
                quantile_returns = perform_[period]['quantile_returns'] #获取每个持有期的分位数收益率Series，索引为分位数，值为对应的累计收益率
                row = {'持有期': period} #创建一个字典，包含持有期信息
                for q, ret in quantile_returns.items(): #遍历每个分位数及其对应的累计收益率，将其添加到字典中，键为分位数，值为累计收益率
                    row[f'Q{q}'] = ret #将分位数收益率添加到字典中，键为Q1、Q2、...、Q10，值为对应的累计收益率
                quantile_returns_data.append(row) #将每个持有期的分位数收益率数据添加到列表中，形成一个字典列表，每个字典包含持有期和对应的分位数收益率
            
            quantile_df = pd.DataFrame(quantile_returns_data) #将分位数收益率数据列表转换为DataFrame，列名为持有期、Q1、Q2、...、Q10
            quantile_df.to_excel(writer, sheet_name='分位数收益', index=False) #将分位数收益率数据写入Excel文件的“分位数收益”工作表中，不包含索引列
            
            ic_data = [] #创建一个空列表，用于存储每个持有期的IC时间序列数据
            for period in perform_.keys(): #遍历每个持有期，获取对应的IC时间序列数据，并将其存储为字典列表
                if 'ic_series' in perform_[period]: #检查每个持有期的绩效数据字典中是否包含IC时间序列，如果存在，则获取该时间序列
                    ic_series = perform_[period]['ic_series'] #获取每个持有期的IC时间序列Series，索引为日期，值为对应的IC值
                    for date, ic_value in ic_series.items(): #遍历每个日期及其对应的IC值，将其添加到字典中，键为持有期、日期和IC值
                        ic_data.append({
                            '持有期': period,
                            '日期': date,
                            'IC值': ic_value
                        }) #将每个持有期的IC时间序列数据添加到列表中，形成一个字典列表，每个字典包含持有期、日期和对应的IC值
            
            if ic_data:
                ic_df = pd.DataFrame(ic_data)
                ic_df.to_excel(writer, sheet_name='IC时间序列', index=False) #将IC时间序列数据写入Excel文件的“IC时间序列”工作表中，不包含索引列
        
        print(f"统计结果已保存到: {output_path}")
    except Exception as e:
        print(f"保存Excel文件时出错: {e}")
    
    return summary_df #返回汇总统计数据的DataFrame，包含每个持有期的因子方向、IC、ICIR、累计收益率、年化收益率、最大回撤和夏普比率等指标

def plot_quantile_returns_separate(perform_): #绘制每个持有期的分位数收益率曲线，区分Top组和Bottom组
    periods = list(perform_.keys()) #获取所有持有期的列表
    
    for period in periods: #遍历每个持有期，获取对应的原始数据DataFrame，并按日期和分位数计算平均收益，得到一个透视表形式的DataFrame，索引为日期，列为分位数，值为对应的平均收益率
        df = perform_[period]['data'] #获取每个持有期的原始数据DataFrame，包括 trade_date、code、factor、return_{period}d 和 quantile 列
        daily_portfolio_returns = df.groupby(['trade_date', 'quantile'])[f'return_{period}d'].mean().reset_index() #按日期和分位数计算平均收益，得到一个DataFrame，包括 trade_date、quantile 和 return_{period}d 列
        returns_pivot = daily_portfolio_returns.pivot(index='trade_date', columns='quantile', values=f'return_{period}d') #将DataFrame透视为宽表，索引为 trade_date，列为 quantile，值为 return_{period}d 的平均值
        returns_pivot = returns_pivot.sort_index() #按日期排序，确保时间序列的正确性
        cumulative_returns = returns_pivot.add(1).cumprod() #计算每个分位数的累计收益率曲线，即每一天的累计收益率 = (1 + 当天收益率) 的累乘积
        
        plt.figure(figsize=(14, 8)) #创建一个新的图形窗口，设置图形大小为14x8英寸
        
        colors = {}
        colors[10] = '#FF3333'  
        colors[1] = '#3333FF'   
        
        middle_group_colors = [
            '#FF6B6B', '#4ECDC4', '#FFD166', '#06D6A0',
            '#118AB2', '#EF476F', '#7B68EE', '#20B2AA'
        ]
        
        for idx, q in enumerate(range(2, 10)): #遍历中间分位数（Q2到Q9），为每个分位数分配颜色，使用预定义的颜色列表
            if q in cumulative_returns.columns: #检查当前分位数是否存在于累计收益率的列中，如果存在，则为该分位数分配颜色
                colors[q] = middle_group_colors[idx] #为中间分位数分配颜色，使用预定义的颜色列表中的颜色，确保每个分位数在图中有不同的颜色表示
        
        quantile_order = sorted(cumulative_returns.columns) #获取累计收益率的分位数列，并按升序排序，确保绘图时分位数的顺序正确
        
        for quantile in quantile_order: #遍历所有分位数，绘制每个分位数的累计收益率曲线，区分Top组和Bottom组
            if quantile not in cumulative_returns.columns: #检查当前分位数是否存在于累计收益率的列中，如果不存在，则跳过该分位数，避免绘图时出现错误
                continue #如果当前分位数不存在于累计收益率的列中，则跳过该分位数，避免绘图时出现错误
            if quantile not in [1, 10]: #检查当前分位数是否为Top组（Q10）或Bottom组（Q1），如果不是，则绘制中间分位数的累计收益率曲线，使用较细的线条和较低的透明度
                plt.plot(cumulative_returns.index, cumulative_returns[quantile],  #绘制中间分位数的累计收益率曲线，使用较细的线条和较低的透明度
                         label=f'Q{quantile}', color=colors[quantile],  
                         linewidth=1.5, alpha=0.8, linestyle='-')
        
        for quantile in [1, 10]: #遍历Top组（Q10）和Bottom组（Q1），绘制其累计收益率曲线，使用较粗的线条和较高的透明度，确保在图中突出显示
            if quantile in cumulative_returns.columns: #检查当前分位数是否存在于累计收益率的列中，如果存在，则绘制该分位数的累计收益率曲线
                label = f'Top组(Q{quantile})' if quantile == 10 else f'Bottom组(Q{quantile})' #为Top组（Q10）和Bottom组（Q1）设置图例标签，确保在图中清晰标识每个分位数的累计收益率曲线
                plt.plot(cumulative_returns.index, cumulative_returns[quantile],  #绘制Top组（Q10）和Bottom组（Q1）的累计收益率曲线，使用较粗的线条和较高的透明度，确保在图中突出显示
                         label=label, color=colors[quantile], 
                         linewidth=3.0, alpha=1.0, linestyle='-')
        
        plt.title(f'{period}天 - 分层收益', fontsize=16, fontweight='bold') #设置图形标题，显示当前持有期的分层收益情况，字体大小为16，字体加粗
        plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left', fontsize=10, framealpha=0.9) #设置图例位置，放置在图形右上角，字体大小为10，图例背景透明度为0.9
        plt.grid(True, alpha=0.3) #显示网格线，设置透明度为0.3，便于观察图形中的数据变化趋势
        
        if len(cumulative_returns) > 10: #如果累计收益率的长度大于10，则在x轴上设置刻度标签，显示每隔一定间隔的日期，避免x轴标签过于密集，影响可读性
            n_ticks = min(8,len(cumulative_returns)) #设置x轴刻度标签的数量，取累计收益率长度和8的最小值，确保刻度标签不会过多，影响图形的可读性
            tick_indices = np.linspace(0, len(cumulative_returns)-1, n_ticks, dtype=int) #生成等间隔的索引，用于在x轴上设置刻度标签，确保刻度标签均匀分布，便于观察图形中的数据变化趋势
            plt.xticks(cumulative_returns.index[tick_indices], rotation=45, fontsize=10)
        
        plt.tight_layout()
        plt.show()

def plot_factor_performance(perfor_):
    periods = list(perfor_.keys())
    
    fig, axes = plt.subplots(2, 2, figsize=(15, 10))
    axes = axes.flatten()
    
    ls_returns = [perfor_[p]['long_short_return'] for p in periods]
    axes[0].bar(range(len(periods)), ls_returns, color='skyblue', alpha=0.8)
    axes[0].set_xticks(range(len(periods)))
    axes[0].set_xticklabels([f'{p}天' for p in periods])
    axes[0].set_title('多空组合收益', fontweight='bold')
    axes[0].set_ylabel('收益')
    for i, v in enumerate(ls_returns):
        axes[0].text(i, v, f'{v:.3f}', ha='center', va='bottom')
    
    ics = [perfor_[p]['ic'] for p in periods]
    axes[1].bar(range(len(periods)), ics, color='lightcoral', alpha=0.8)
    axes[1].set_xticks(range(len(periods)))
    axes[1].set_xticklabels([f'{p}天' for p in periods])
    axes[1].set_title('信息系数(IC)', fontweight='bold')
    axes[1].set_ylabel('IC')
    for i, v in enumerate(ics):
        axes[1].text(i, v, f'{v:.3f}', ha='center', va='bottom')
    
    if periods:
        first_period = periods[0]
        quantile_returns = perfor_[first_period]['quantile_returns']
        axes[2].plot(quantile_returns.index, quantile_returns.values, 
                   marker='o', linewidth=2, markersize=8, color='blue')
        axes[2].set_title(f'{first_period}天期分位数收益', fontweight='bold')
        axes[2].set_xlabel('分位数')
        axes[2].set_ylabel('平均收益')
        axes[2].grid(True, alpha=0.6)
        
        for i, v in enumerate(quantile_returns.values):
            axes[2].text(quantile_returns.index[i], v, f'{v:.3f}', 
                        ha='center', va='bottom')
    
    if periods and 'ic_series' in perfor_[periods[0]]:
        ic_series = perfor_[periods[0]]['ic_series']
        axes[3].plot(ic_series.index, ic_series.values, 
                   linewidth=1, color='purple', alpha=0.7)
        axes[3].axhline(y=ic_series.mean(), color='red', linestyle='--', 
                      label=f'均值: {ic_series.mean():.3f}')
        axes[3].set_title(f'{periods[0]}天期IC时间序列', fontweight='bold')
        axes[3].set_xlabel('日期')
        axes[3].set_ylabel('IC')
        axes[3].legend()
        axes[3].grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.show()


###############################################################################
#### data & fct ####

#### 读取数据 ####
price_data = pd.read_parquet('price_data.parquet')

#### 构建因子 ####


#### 添加因子 ####
fct_df = pd.read_parquet('fct_df.parquet')
factor_names = fct_df.columns
price_data = pd.concat([price_data,fct_df],axis=1,join='inner') # 将因子数据与价格数据合并，只保留两者共有的股票和日期，确保数据对齐，避免因缺失值导致的计算错误
#把 fct_df 的所有列，直接横向追加到 price_data右边，结果重新赋值给 price_data 覆盖原数据

#### 数据处理 ####
price_data[factor_names] = extreme_MAD(price_data[factor_names]) #对因子数据进行极值处理，使用中位数绝对偏差（MAD）方法，将因子值限制在中位数上下5.2倍MAD的范围内，减少异常值对回测结果的影响
price_data[factor_names] = standardize_z(price_data[factor_names].fillna(0)) #对因子数据进行标准化处理，使用Z-score方法，将因子值转换为均值为0、标准差为1的标准正态分布，便于不同因子之间的比较和分析
price_data = preprocess_data(price_data) #对价格数据进行预处理，剔除ST股票和上市不足一年的股票，确保样本池的质量和稳定性

#### 因子回测 ####
# 测试 fct_2
factor_df = price_data['fct_2'] # 提取因子数据


###############################################################################
#### 单因子回测 ####
print('*******************')
print(price_data)
results = factor_analysis(factor_df, price_data, periods=(1, 5, 10, 20), quantiles=10)
performance = analyze_factor_performance(results)
summary_df = generate_summary_statistics(performance, '单因子回测统计结果.xlsx')

print("\n=== 详细统计结果汇总 ===")
print(summary_df.to_string(index=False, float_format='%.4f'))

# 绘制传统因子表现图
plot_factor_performance(performance) 
plot_quantile_returns_separate(performance)





