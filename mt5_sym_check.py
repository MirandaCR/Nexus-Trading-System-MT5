import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.chdir(r"C:\Users\Asus\Desktop\Claude Projects\nexus-trader")
from dotenv import dotenv_values
env = dotenv_values(".env")
import MetaTrader5 as mt5
mt5.initialize(login=int(env["MT5_LOGIN"]), password=env["MT5_PASSWORD"], server=env["MT5_SERVER"], path=env["MT5_PATH"])
info = mt5.account_info()
print(f"Account: #{info.login}  Balance: {info.balance:.2f} {info.currency}")
aliases_raw = env.get("MT5_SYMBOL_ALIASES", "")
stream = env.get("STREAM_SYMBOLS", "")
print(f"STREAM_SYMBOLS: {stream}")
print(f"MT5_SYMBOL_ALIASES: {aliases_raw}")
aliases = {}
for part in aliases_raw.split(";"):
    if "=" in part:
        k,v = part.split("=",1)
        aliases[k.strip().upper()] = v.strip()
print("\nFinal connection test:")
for sym in stream.split(","):
    sym = sym.strip().upper()
    broker_sym = aliases.get(sym, sym)
    ok = mt5.symbol_select(broker_sym, True)
    tick = mt5.symbol_info_tick(broker_sym) if ok else None
    price = f"bid={tick.bid:.5f}" if tick else "no tick"
    print(f"  {sym} -> {broker_sym}: {'OK' if ok else 'FAIL'}  {price}")
mt5.shutdown()
print("\nAll done.")
