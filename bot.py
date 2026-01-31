import os
import time
import yfinance as yf
import pandas as pd
import numpy as np
from scipy.signal import argrelextrema
from hyperliquid.utils import constants
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from eth_account import Account
from datetime import datetime

# --- Configuration ---
# Save these in your environment variables or a .env file (NEVER commit keys to GitHub)
PRIVATE_KEY = os.getenv("HL_PRIVATE_KEY")
ACCOUNT_ADDRESS = os.getenv("HL_ACCOUNT_ADDRESS")

# Trading Settings
TICKER_YF = "BTC-USD"   # Data source
SYMBOL_HL = "BTC"       # Hyperliquid Symbol
LEVERAGE = 3            # Leverage multiplier
PERCENTAGE_PER_TRADE = 0.20  # Use 20% of account value per trade
API_URL = constants.TESTNET_API_URL  # Switch to MAINNET_API_URL for real money

def log_operation(action, price, size, pnl, balance):
    """Logs trades to a CSV file."""
    file_name = "trading_history.csv"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Create record
    new_data = pd.DataFrame([{
        "Date": now,
        "Action": action,
        "Price": price,
        "Size_BTC": size,
        "PnL_Realized": pnl,
        "Account_Balance": balance
    }])
    
    # Append to file
    if not os.path.isfile(file_name):
        new_data.to_csv(file_name, index=False)
    else:
        new_data.to_csv(file_name, mode='a', header=False, index=False)
    
    print(f"[{now}] {action}: {size} BTC @ ${price:.2f} | Bal: ${balance:.2f}")

def get_signal():
    """
    Analyzes 'BTC-USD' data to determine BUY (Long), SELL (Short), or HOLD.
    Returns: Signal (str), Current Price (float)
    """
    try:
        # Download recent data (enough for order=5 calculation)
        data = yf.download(TICKER_YF, period="5d", interval="1h", progress=False)
        
        # Handle MultiIndex columns if present
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
            
        if data.empty:
            return "HOLD", 0

        # Calculate local mins and maxes
        data['local_min'] = data.iloc[argrelextrema(data['Close'].values, np.less_equal, order=5)[0]]['Close']
        data['local_max'] = data.iloc[argrelextrema(data['Close'].values, np.greater_equal, order=5)[0]]['Close']
        
        last_price = float(data['Close'].iloc[-1])
        prev_price = float(data['Close'].iloc[-2])
        
        # Look at the last 10 candles for relevant support/resistance
        recent_min = data['local_min'].iloc[-10:].dropna()
        recent_max = data['local_max'].iloc[-10:].dropna()

        # BUY Logic: Recent local min exists AND price is moving up
        if not recent_min.empty and last_price > prev_price:
            return "BUY", last_price

        # SELL Logic: Recent local max exists AND price is moving down
        if not recent_max.empty and last_price < prev_price:
            return "SELL", last_price
        
        return "HOLD", last_price
        
    except Exception as e:
        print(f"Error getting signal: {e}")
        return "HOLD", 0

def execute_trade():
    if not PRIVATE_KEY or not ACCOUNT_ADDRESS:
        print("Error: Private Key or Address not found in environment variables.")
        return

    # Initialize connection
    account = Account.from_key(PRIVATE_KEY)
    info = Info(API_URL, skip_ws=True)
    exchange = Exchange(account, API_URL)

    # 1. Get Account State
    user_state = info.user_state(ACCOUNT_ADDRESS)
    margin_summary = user_state.get('marginSummary', {})
    balance = float(margin_summary.get('accountValue', 0)) # Use Total Account Value
    
    # 2. Check Current Position
    positions = user_state.get('assetPositions', [])
    current_position = None
    position_size = 0.0
    
    for pos in positions:
        if pos['position']['coin'] == SYMBOL_HL:
            sze = float(pos['position']['szi'])
            if sze > 0:
                current_position = "LONG"
                position_size = sze
            elif sze < 0:
                current_position = "SHORT"
                position_size = abs(sze)
            break

    # 3. Get Signal
    signal, price = get_signal()
    if price == 0: return

    print(f"Signal: {signal} | Price: {price} | Current Pos: {current_position} | Bal: {balance}")

    # 4. Execute Logic
    
    # --- SCENARIO: OPEN LONG ---
    if signal == "BUY":
        if current_position == "SHORT":
            # Flip: Close Short first
            print("Closing Short to Flip Long...")
            exchange.market_close(SYMBOL_HL)
            time.sleep(2) # Wait for processing
            current_position = None # Reset
            
        if current_position is None:
            # Calculate size
            amount_usd = balance * PERCENTAGE_PER_TRADE
            quantity_btc = round((amount_usd * LEVERAGE) / price, 4)
            
            # Execute
            print(f"Opening LONG with ${amount_usd:.2f} ({quantity_btc} BTC)...")
            exchange.update_leverage(LEVERAGE, SYMBOL_HL)
            exchange.market_open(SYMBOL_HL, is_buy=True, sz=quantity_btc, px=None, slippage=0.01)
            log_operation("OPEN LONG", price, quantity_btc, 0, balance)

    # --- SCENARIO: OPEN SHORT ---
    elif signal == "SELL":
        if current_position == "LONG":
            # Flip: Close Long first
            print("Closing Long to Flip Short...")
            exchange.market_close(SYMBOL_HL)
            time.sleep(2) # Wait for processing
            current_position = None # Reset

        if current_position is None:
            # Calculate size
            amount_usd = balance * PERCENTAGE_PER_TRADE
            quantity_btc = round((amount_usd * LEVERAGE) / price, 4)
            
            # Execute (is_buy=False for Short)
            print(f"Opening SHORT with ${amount_usd:.2f} ({quantity_btc} BTC)...")
            exchange.update_leverage(LEVERAGE, SYMBOL_HL)
            exchange.market_open(SYMBOL_HL, is_buy=False, sz=quantity_btc, px=None, slippage=0.01)
            log_operation("OPEN SHORT", price, quantity_btc, 0, balance)
            
    else:
        print("Holding...")

if __name__ == "__main__":
    # You might want to run this in a loop or via cron job
    execute_trade()
