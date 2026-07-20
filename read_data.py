import pandas as pd

path = "C:\Users\Administrator\Documents\GitHub\fct_backtest\minute_price_all_5344.parquet"

df = pd.read_parquet(path)
print(df.head())
