import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

def get_analysis(ticker_symbol):
    asset = yf.Ticker(ticker_symbol)
    hist = asset.history(period="1y")
    
    # Technicals
    hist['MA50'] = hist['Close'].rolling(window=50).mean()
    hist['MA200'] = hist['Close'].rolling(window=200).mean()
    
    delta = hist['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    hist['RSI'] = 100 - (100 / (1 + rs))
    
    latest = hist.iloc[-1]
    price = latest['Close']
    ma50 = latest['MA50']
    ma200 = latest['MA200']
    rsi = latest['RSI']
    
    status = ""
    if price > ma50 > ma200: status = "Strong Bullish (Right-side confirmed)"
    elif ma50 > price > ma200: status = "Neutral/Correction (Between MA50/MA200)"
    elif ma50 > ma200 > price: status = "Bearish (Oversold territory?)"
    else: status = "Structural Breakdown"

    # Options (Jan 2028 LEAPS)
    leaps_info = "N/A"
    try:
        expirations = asset.options
        jan_28 = [e for e in expirations if e.startswith('2028-01')]
        if jan_28:
            opt = asset.option_chain(jan_28[0])
            calls = opt.calls
            target_strike = price * 0.75
            idx = (np.abs(calls['strike'] - target_strike)).argmin()
            call = calls.iloc[idx]
            
            iv = call['impliedVolatility']
            intrinsic = price - call['strike']
            extrinsic = ((call['bid'] + call['ask'])/2) - intrinsic
            dte = (pd.to_datetime(jan_28[0]) - pd.Timestamp.today()).days
            cost = (extrinsic / price) / (dte / 365.25) * 100
            leaps_info = f"Strike: {call['strike']}, IV: {iv:.1%}, Annual Cost: {cost:.2f}%"
    except: pass

    return {
        "Ticker": ticker_symbol,
        "Price": round(price, 2),
        "MA50": round(ma50, 2),
        "MA200": round(ma200, 2),
        "RSI": round(rsi, 2),
        "Status": status,
        "LEAPS": leaps_info
    }

assets = ["QQQ", "VOO"]
results = [get_analysis(a) for a in assets]

# Generate Markdown Report
report = f"# 🚀 LEAPS Strategy Daily Diagnosis - {datetime.now().strftime('%Y-%m-%d')}

"
for res in results:
    report += f"## {res['Ticker']}
"
    report += f"- **Current Price:** ${res['Price']}
"
    report += f"- **MA50 / MA200:** ${res['MA50']} / ${res['MA200']}
"
    report += f"- **RSI (14):** {res['RSI']}
"
    report += f"- **Trend Status:** `{res['Status']}`
"
    report += f"- **Jan 2028 LEAPS (0.8 Delta):** {res['LEAPS']}

"

with open("DAILY_REPORT.md", "w") as f:
    f.write(report)
print("Analysis complete. Generated DAILY_REPORT.md")
