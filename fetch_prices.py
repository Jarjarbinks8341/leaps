import yfinance as yf
import sqlite3
import pandas as pd
from datetime import datetime

# Define ticker symbols and date range
tickers = ["QQQ", "VOO"]
start_date = "2025-01-01"
end_date = datetime.now().strftime("%Y-%m-%d")

# Create a connection to a local SQLite database
db_name = "stock_prices.db"
conn = sqlite3.connect(db_name)

def fetch_and_save(ticker, start, end):
    print(f"Fetching data for {ticker}...")
    # Fetch historical daily data
    data = yf.download(ticker, start=start, end=end)
    
    if data.empty:
        print(f"No data found for {ticker}.")
        return

    # Flatten the multi-index columns if they exist (yfinance 0.2.x handles this)
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = [col[0] for col in data.columns]
    
    # Ensure index is a column (Date)
    data.reset_index(inplace=True)
    
    # Add a column for the ticker
    data['Ticker'] = ticker

    # Write the data to a SQLite table (append if it exists)
    data.to_sql("daily_prices", conn, if_exists="append", index=False)
    print(f"Saved {len(data)} rows for {ticker} to {db_name}.")

# Clear existing table if necessary, or just append
# conn.execute("DROP TABLE IF EXISTS daily_prices")

for ticker in tickers:
    fetch_and_save(ticker, start_date, end_date)

# Close the database connection
conn.close()
print("All done.")
