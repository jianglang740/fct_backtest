# -*- coding: utf-8 -*-
"""
Created on Tue Dec  2 13:40:27 2025

@author: andrew
"""

###############################################################################
#### import ####

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from scipy import stats
import warnings
warnings.filterwarnings('ignore')

plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False


###############################################################################
#### func ####
def extreme_MAD(dt, n=5.2):
    median = dt.quantile(0.5)
    new_median = (abs((dt - median)).quantile(0.5))
    dt_up = median + n*new_median
    dt_down = median - n*new_median
    return dt.clip(dt_down, dt_up, axis=1)

def standardize_z(dt):
    mean = dt.mean()
    std = dt.std()
    return (dt - mean)/std

def preprocess_data(data):
    df = data.copy()
    mask = (~df['is_st']) & (df['trade_days'] >= 365)
    df = df[mask].copy()
    df.drop(['is_st','trade_days'], axis=1, inplace=True)
    return df

def factor_analysis(factor_series, price_df, periods=(1, 5, 10), quantiles=10):
    results = {}
    price_aligned = pd.pivot_table(price_df['close_adj'].reset_index(),index='trade_date', columns='code', values='close_adj')
    
    for period in periods:
        future_prices = price_aligned.shift(-period-1)
        current_prices = price_aligned.shift(-1)
        future_returns = (future_prices / current_prices - 1)
        factor_df = factor_series.reset_index()
        factor_df.columns = ['trade_date', 'code', 'factor']
        
        future_returns = future_returns.iloc[::period].dropna(axis=0,how='all')
        
        returns_series = future_returns.stack()
        returns_series.name = f'return_{period}d'
        merged_df = pd.merge(factor_df, returns_series, 
                           left_on=['trade_date', 'code'], 
                           right_index=True)
        
        merged_df = merged_df.dropna(subset=['factor', f'return_{period}d'])
        merged_df = merged_df.sort_values('trade_date')
        
        if len(merged_df) > 0:
            merged_df['quantile'] = merged_df.groupby('trade_date')['factor'].transform(
                lambda x: pd.qcut(x, quantiles, labels=False, duplicates='drop') + 1
            )
            results[period] = merged_df
    
    return results

def analyze_factor_performance(res_):
    perform_ = {}
    
    for period, df in res_.items():
        quantile_returns = df.groupby(['trade_date','quantile'])[f'return_{period}d'].mean()
        quantile_returns = quantile_returns.reset_index()
        quantile_returns = pd.pivot_table(quantile_returns,index='trade_date',columns='quantile',values=f'return_{period}d')
        quantile_returns = quantile_returns.add(1).cumprod().sub(1).iloc[-1]
        
        long_short_return = quantile_returns.iloc[-1] - quantile_returns.iloc[0]
        
        ic_series = df.groupby('trade_date').apply(
            lambda x: stats.spearmanr(x['factor'], x[f'return_{period}d']).correlation 
            if len(x) > 5 else np.nan
        ).dropna()
        
        if len(ic_series) > 0:
            ic = ic_series.mean()
            ic_ir = ic_series.mean() / ic_series.std() if ic_series.std() > 0 else 0
            
            perform_[period] = {
                'quantile_returns': quantile_returns,
                'long_short_return': long_short_return,
                'ic': ic,
                'ic_ir': ic_ir,
                'ic_series': ic_series,
                'data': df
            }
    
    return perform_

def calculate_portfolio_metrics(returns_series, period_days=1):
    if len(returns_series) == 0:
        return {}
    
    cumulative_curve = (1 + returns_series).cumprod()
    cumulative_return = cumulative_curve.iloc[-1] - 1 if len(cumulative_curve) > 0 else 0
    
    if len(returns_series) > 0:
        total_days = len(returns_series) * period_days
        annual_return = (1 + cumulative_return) ** (252 / total_days) - 1
    else:
        annual_return = 0
    
    running_max = cumulative_curve.expanding().max()
    drawdown = (cumulative_curve - running_max) / running_max
    max_drawdown = abs(drawdown.min())
    
    if len(returns_series) > 1:
        annual_volatility = returns_series.std() * np.sqrt(252 / period_days)
    else:
        annual_volatility = 0
    
    sharpe_ratio = annual_return / annual_volatility if annual_volatility > 0 else 0
    
    downside_returns = returns_series[returns_series < 0]
    if len(downside_returns) > 1:
        downside_volatility = downside_returns.std() * np.sqrt(252 / period_days)
    else:
        downside_volatility = 0
    sortino_ratio = annual_return / downside_volatility if downside_volatility > 0 else 0
    
    calmar_ratio = annual_return / max_drawdown if max_drawdown > 0 else 0
    
    return {
        '累计收益率': cumulative_return,
        '年化收益率': annual_return,
        '最大回撤': -max_drawdown,
        '夏普比率': sharpe_ratio,
        '索提诺比率': sortino_ratio,
        '卡玛比率': calmar_ratio,
       
    }

def determine_factor_direction(perfo_):
    direction_info = {}
    
    for period, perf in perfo_.items():
        long_short_return = perf['long_short_return']
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
        }
    
    return direction_info

def generate_summary_statistics(perform_, output_path='因子回测统计结果.xlsx'):
    direction_info = determine_factor_direction(perform_)    
    summary_data = []
    
    for period in perform_.keys():
        df = perform_[period]['data']
        direction_data = direction_info[period]        
        daily_portfolio_returns = df.groupby(['trade_date', 'quantile'])[f'return_{period}d'].mean().reset_index()
        returns_pivot = daily_portfolio_returns.pivot(index='trade_date', columns='quantile', values=f'return_{period}d')
        returns_pivot = returns_pivot.sort_index()
        target_quantile = direction_data['target_quantile']
        
        if target_quantile in returns_pivot.columns:
            target_returns = returns_pivot[target_quantile].dropna()
        else:
            print(f"警告: 持有期{period}天没有找到分位数{target_quantile}")
            continue
        
        metrics = calculate_portfolio_metrics(target_returns, period)
        
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
        
        summary_data.append(period_summary)
    
    if not summary_data:
        return pd.DataFrame()
        
    summary_df = pd.DataFrame(summary_data)
    
    try:
        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            summary_df.to_excel(writer, sheet_name='汇总统计', index=False)
            
            quantile_returns_data = []
            for period in perform_.keys():
                quantile_returns = perform_[period]['quantile_returns']
                row = {'持有期': period}
                for q, ret in quantile_returns.items():
                    row[f'Q{q}'] = ret
                quantile_returns_data.append(row)
            
            quantile_df = pd.DataFrame(quantile_returns_data)
            quantile_df.to_excel(writer, sheet_name='分位数收益', index=False)
            
            ic_data = []
            for period in perform_.keys():
                if 'ic_series' in perform_[period]:
                    ic_series = perform_[period]['ic_series']
                    for date, ic_value in ic_series.items():
                        ic_data.append({
                            '持有期': period,
                            '日期': date,
                            'IC值': ic_value
                        })
            
            if ic_data:
                ic_df = pd.DataFrame(ic_data)
                ic_df.to_excel(writer, sheet_name='IC时间序列', index=False)
        
        print(f"统计结果已保存到: {output_path}")
    except Exception as e:
        print(f"保存Excel文件时出错: {e}")
    
    return summary_df

def plot_quantile_returns_separate(perform_):
    periods = list(perform_.keys())
    
    for period in periods:
        df = perform_[period]['data']
        daily_portfolio_returns = df.groupby(['trade_date', 'quantile'])[f'return_{period}d'].mean().reset_index()
        returns_pivot = daily_portfolio_returns.pivot(index='trade_date', columns='quantile', values=f'return_{period}d')
        returns_pivot = returns_pivot.sort_index()
        cumulative_returns = returns_pivot.add(1).cumprod()
        
        plt.figure(figsize=(14, 8))
        
        colors = {}
        colors[10] = '#FF3333'  
        colors[1] = '#3333FF'   
        
        middle_group_colors = [
            '#FF6B6B', '#4ECDC4', '#FFD166', '#06D6A0',
            '#118AB2', '#EF476F', '#7B68EE', '#20B2AA'
        ]
        
        for idx, q in enumerate(range(2, 10)):
            if q in cumulative_returns.columns:
                colors[q] = middle_group_colors[idx]
        
        quantile_order = sorted(cumulative_returns.columns)
        
        for quantile in quantile_order:
            if quantile not in cumulative_returns.columns:
                continue
            if quantile not in [1, 10]:
                plt.plot(cumulative_returns.index, cumulative_returns[quantile], 
                         label=f'Q{quantile}', color=colors[quantile], 
                         linewidth=1.5, alpha=0.8, linestyle='-')
        
        for quantile in [1, 10]:
            if quantile in cumulative_returns.columns:
                label = f'Top组(Q{quantile})' if quantile == 10 else f'Bottom组(Q{quantile})'
                plt.plot(cumulative_returns.index, cumulative_returns[quantile], 
                         label=label, color=colors[quantile], 
                         linewidth=3.0, alpha=1.0, linestyle='-')
        
        plt.title(f'{period}天 - 分层收益', fontsize=16, fontweight='bold')
        plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left', fontsize=10, framealpha=0.9)
        plt.grid(True, alpha=0.3)
        
        if len(cumulative_returns) > 10:
            n_ticks = min(8, len(cumulative_returns))
            tick_indices = np.linspace(0, len(cumulative_returns)-1, n_ticks, dtype=int)
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
fct_df = pd.read_parquet('mom_fct_df.parquet')
factor_names = fct_df.columns
price_data = pd.concat([price_data,fct_df],axis=1,join='inner')


#### 数据处理 ####
price_data[factor_names] = extreme_MAD(price_data[factor_names])
price_data[factor_names] = standardize_z(price_data[factor_names].fillna(0))
price_data = preprocess_data(price_data)

#### 因子回测 ####
# 测试 fct_1
factor_df = price_data['mom_20d']


###############################################################################
#### 单因子回测 ####
print('*******************')
print(price_data)
results = factor_analysis(factor_df, price_data, periods=(1, 5, 10, 20), quantiles=10)
performance = analyze_factor_performance(results)
summary_df = generate_summary_statistics(performance, '20日动量因子回测统计结果.xlsx')

print("\n=== 详细统计结果汇总 ===")
print(summary_df.to_string(index=False, float_format='%.4f'))

# 绘制传统因子表现图
plot_factor_performance(performance)
plot_quantile_returns_separate(performance)
