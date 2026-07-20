import pandas as pd

price_data = pd.read_parquet("price_data.parquet")
fct_df = pd.read_parquet('fct_df.parquet')
mom_df = pd.read_parquet('mom_fct_df.parquet')

data = pd.concat([price_data,fct_df,mom_df],axis=1,join='inner') 
print(data.head())

data.to_parquet('data.parquet')