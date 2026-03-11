import sqlite3
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime

# Connect to the local database
db_name = "stock_prices.db"
conn = sqlite3.connect(db_name)

# Read QQQ data from the database
query = "SELECT Date, Close FROM daily_prices WHERE Ticker = 'QQQ' ORDER BY Date ASC"
df = pd.read_sql_query(query, conn)

# Ensure Date column is in datetime format
df['Date'] = pd.to_datetime(df['Date'])
df.set_index('Date', inplace=True)

# Calculate MA50 and MA200
df['MA50'] = df['Close'].rolling(window=50).mean()
df['MA200'] = df['Close'].rolling(window=200).mean()

# Filter data for display (from Jan 1st, 2025)
df_display = df[df.index >= '2025-01-01']

# Plotting the data
plt.figure(figsize=(12, 6))
plt.plot(df_display.index, df_display['Close'], label='QQQ Price', color='blue', linewidth=1)
plt.plot(df_display.index, df_display['MA50'], label='MA50', color='orange', linestyle='--')
plt.plot(df_display.index, df_display['MA200'], label='MA200', color='red', linestyle='-.')

plt.title('QQQ Price and Moving Averages (Since Jan 1st, 2025)')
plt.xlabel('Date')
plt.ylabel('Price (USD)')
plt.legend()
plt.grid(True)
plt.tight_layout()

# Save the plot to a file
plot_filename = "qqq_analysis.png"
plt.savefig(plot_filename)
print(f"Plot saved as {plot_filename}.")

# Close the database connection
conn.close()
