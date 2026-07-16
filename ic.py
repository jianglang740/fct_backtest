import pandas as pd
import matplotlib.pyplot as plt

# ===================== 1. 读取Excel文件 =====================
# 请将此处替换为你的Excel文件实际路径
file_path = "单因子回测统计结果.xlsx"
# 读取指定sheet，自动识别表头
df = pd.read_excel(file_path, sheet_name="IC时间序列")

# 查看数据前5行，确认读取是否正常
print("数据预览：")
print(df.head())

# ===================== 2. 数据预处理 =====================
# 转换日期列为datetime格式，确保时序正确
df["日期"] = pd.to_datetime(df["日期"])
# 按日期升序排序（防止Excel中日期乱序）
df = df.sort_values("日期").reset_index(drop=True)

# 计算核心统计指标，标注在图上
ic_mean = df["IC值"].mean()
ic_std = df["IC值"].std()
ic_ir = ic_mean / ic_std
print(f"\nIC核心统计：")
print(f"均值IC: {ic_mean:.4f}")
print(f"IC标准差: {ic_std:.4f}")
print(f"ICIR: {ic_ir:.4f}")

# ===================== 3. 绘制IC时序图 =====================
# 设置中文字体，防止中文乱码
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS'] #Arial Unicode MS
plt.rcParams['axes.unicode_minus'] = False

# 创建画布
plt.figure(figsize=(14, 7), dpi=100)

# 绘制IC时序曲线
plt.plot(df["日期"], df["IC值"], color="#1f77b4", linewidth=1.2, label="单日IC值")
# 绘制IC均值水平线
plt.axhline(y=ic_mean, color="#ff7f0e", linestyle="--", linewidth=1.5, label=f"均值IC: {ic_mean:.4f}")
# 绘制0值基准线
plt.axhline(y=0, color="#d62728", linestyle="-", linewidth=1, label="0基准线")

# 图表美化与标注
plt.title("因子IC时间序列走势", fontsize=16, fontweight="bold")
plt.xlabel("日期", fontsize=12)
plt.ylabel("IC值", fontsize=12)
plt.legend(fontsize=10)
plt.grid(True, alpha=0.3, linestyle="--")
plt.xticks(rotation=45)  # 日期标签旋转，防止重叠
plt.tight_layout()  # 自动调整布局

# 保存图片（可选）
plt.savefig("IC时序图.png", dpi=300, bbox_inches="tight")
# 显示图表
plt.show()