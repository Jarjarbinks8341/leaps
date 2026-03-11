import yfinance as yf
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

qqq = yf.Ticker("QQQ")
hist = qqq.history(period="1y")

hist['MA50'] = hist['Close'].rolling(window=50).mean()
hist['MA200'] = hist['Close'].rolling(window=200).mean()

delta = hist['Close'].diff()
gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
rs = gain / loss
hist['RSI'] = 100 - (100 / (1 + rs))

exp1 = hist['Close'].ewm(span=12, adjust=False).mean()
exp2 = hist['Close'].ewm(span=26, adjust=False).mean()
hist['MACD'] = exp1 - exp2
hist['Signal_Line'] = hist['MACD'].ewm(span=9, adjust=False).mean()

latest = hist.iloc[-1]
price = latest['Close']
ma50 = latest['MA50']
ma200 = latest['MA200']
rsi = latest['RSI']
macd = latest['MACD']
macd_signal = latest['Signal_Line']

print(f"--- Technicals ---")
print(f"Price: {price:.2f}")
print(f"MA50: {ma50:.2f}")
print(f"MA200: {ma200:.2f}")
print(f"RSI: {rsi:.2f}")
print(f"MACD: {macd:.2f}, Signal: {macd_signal:.2f}")

try:
    expirations = qqq.options
    jan_2028_exps = [exp for exp in expirations if exp.startswith('2028-01')]
    if jan_2028_exps:
        target_exp = jan_2028_exps[0]
        opt = qqq.option_chain(target_exp)
        calls = opt.calls
        
        target_strike = price * 0.75 
        
        idx = (np.abs(calls['strike'] - target_strike)).argmin()
        target_call = calls.iloc[idx]
        
        strike = target_call['strike']
        opt_price = (target_call['bid'] + target_call['ask']) / 2 if target_call['bid'] > 0 else target_call['lastPrice']
        iv = target_call['impliedVolatility']
        
        intrinsic_value = price - strike
        extrinsic_value = opt_price - intrinsic_value
        dte = (pd.to_datetime(target_exp) - pd.Timestamp.today()).days
        annual_cost = (extrinsic_value / price) / (dte / 365.25) * 100
        
        print(f"--- Options Data (Exp: {target_exp}) ---")
        print(f"Strike: {strike}")
        print(f"Option Price: {opt_price:.2f}")
        print(f"IV: {iv:.2%}")
        print(f"Intrinsic: {intrinsic_value:.2f}")
        print(f"Extrinsic: {extrinsic_value:.2f}")
        print(f"Annual 'Interest' Cost: {annual_cost:.2f}%")
    else:
        print("Jan 2028 Expiration not found.")
except Exception as e:
    print(f"Error fetching options: {e}")
