# 🛠️ Setup & Deployment Guide — Nexus Trader

This guide walks you from a fresh clone to a running system. If you only read one thing, read **[Platform & MT5 — read this first](#-platform--mt5--read-this-first)**.

> 🇪🇸 ¿Prefieres español? El [README en español](../README.es.md) tiene el mismo flujo de instalación resumido.

---

## ⚡ TL;DR (the whole thing in 6 commands)

```bash
git clone https://github.com/MirandaCR/Nexus-Trading-System-MT5.git
cd Nexus-Trading-System-MT5
python -m venv .venv && .venv\Scripts\activate          # Windows
pip install -r requirements.txt
cd dashboard && npm install && cd ..
copy .env.example .env                                    # then edit .env (see below)
docker run -d --name nexus-redis -p 6379:6379 redis:7-alpine
python run.py
```

Then open **http://localhost:3100**. `run.py` runs a pre-flight and won't start if something critical is missing — the error tells you exactly what.

---

## 🖥️ Platform & MT5 — read this first

Nexus connects to the market through the **`MetaTrader5` Python package, which only runs on Windows.** That shapes everything:

| You are on… | What works |
|-------------|-----------|
| **Windows** (recommended) | Everything — live MT5 data, order execution, the full pipeline |
| **macOS / Linux** | Everything **except** live MT5 data & execution. The 9 services, dashboard, ML and agents still boot, but `nexus-data`/`nexus-executor` can't reach MT5. Good for reading the code and the UI, not for trading. |

For a real run you need the **MetaTrader 5 desktop terminal installed, running, logged into a demo account, with *Algo Trading* enabled.** More on that in [Step 4](#step-4--metatrader-5-demo-account).

> This repo ships **no credentials** and is wired to a **demo** account. It is a research/portfolio project — not financial advice, not a money printer. Leveraged instruments can wipe an account.

---

## ✅ Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| **Python** | **3.11** | 3.11 is the tested target. (`pandas-ta` needs ≥3.12 on PyPI, so the repo uses `pandas-ta-classic` as a 3.11-friendly fallback.) |
| **Node.js** | **18+** | For the Next.js dashboard. |
| **Docker Desktop** | any recent | Only used to run **Redis**. Or point to any Redis on `:6379`. |
| **MetaTrader 5** | latest | The desktop terminal, logged into a **demo** account (Windows). |
| **DeepSeek / OpenAI / Gemini API key** | optional | Enables the GenAI agents. The quant ML ensemble runs **without** any key. |

---

## Step 1 — Clone

```bash
git clone https://github.com/MirandaCR/Nexus-Trading-System-MT5.git
cd Nexus-Trading-System-MT5
```

## Step 2 — Python environment + dependencies

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
```

## Step 3 — Dashboard dependencies

```bash
cd dashboard
npm install
cd ..
```

## Step 4 — MetaTrader 5 (demo account)

1. Install the **MetaTrader 5** desktop terminal from your broker (XM, etc.).
2. Open a **demo** account and log in.
3. Enable automated trading: **Tools → Options → Expert Advisors → "Allow algorithmic trading"**, and make sure the **Algo Trading** button in the toolbar is green.
4. Note three things for your `.env`:
   - **Login** (account number)
   - **Password** (the demo password)
   - **Server** (e.g. `XMGlobal-MT5 5` — exactly as shown in the terminal)
   - Optionally the **path** to `terminal64.exe`.

> ⚠️ **Most common trap:** the `MT5_LOGIN` / `MT5_SERVER` in your `.env` must match **the account the terminal is currently logged into.** A mismatch (e.g. `.env` points to a different account) produces `MT5 initialize failed: [-6] Authorization failed`. Keep them in sync.

## Step 5 — Redis

Pick one:

```bash
# Option A — one container
docker run -d --name nexus-redis -p 6379:6379 redis:7-alpine
```

```bash
# Option B — the project's compose file (also wires the services)
docker compose up -d redis
```

Verify it answers:

```bash
docker exec nexus-redis redis-cli ping    # -> PONG
```

## Step 6 — Configure `.env`

Copy the template and fill in **your** values. Everything is documented inside `.env.example`.

```bash
copy .env.example .env      # Windows   (cp .env.example .env on macOS/Linux)
```

The essentials:

| Variable | What to put |
|----------|-------------|
| `MT5_LOGIN` | Your demo account number |
| `MT5_PASSWORD` | Your demo password |
| `MT5_SERVER` | Your broker server, exactly as shown (e.g. `XMGlobal-MT5 5`) |
| `MT5_PATH` | *(optional)* full path to `terminal64.exe` |
| `STREAM_SYMBOLS` | Symbols to trade, e.g. `XAUUSD,BTCUSD` |
| `NEXUS_LLM_PROVIDER` | `deepseek` (default) · `openai` · `gemini` · `local` |
| `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` | key for the provider you chose (only one needed; leave blank to run quant-only) |
| `NEXUS_FIXED_LOTS` | per-symbol fixed lot, e.g. `BTCUSD=0.02` |
| `NEXUS_LOG_JSON` | `false` for readable console logs, `true` for JSON |

> 🔒 **Never commit your `.env`.** It's already in `.gitignore`. Only `.env.example` (empty placeholders) is tracked.

## Step 7 — Run it

```bash
python run.py
```

`run.py` is the supervisor: it runs a **pre-flight** check, then boots all **9 services + the dashboard**, injects the inter-service URLs, and auto-restarts anything that crashes.

The pre-flight verifies, and **blocks startup** if a critical one fails:

- ✅ `.env` present and MT5 credentials set
- ✅ **Redis** reachable on `:6379`
- ✅ **MT5** connectivity (balance + symbols)
- ✅ ML models folder present
- ⚠️ warns (non-blocking) if *AutoTrading* is off or you're off-session

Bypass the pre-flight only if you know why: `python run.py --skip-preflight`.

## Step 8 — Open the dashboard

Go to **http://localhost:3100**.

- First load compiles on-demand (Next.js dev) — give it a few seconds.
- Check **System Health** (Risk tab) shows the services ONLINE.
- The **Overview** tab should show the live chart and session clock; **Agents** shows the pipeline graph.

---

## 🧠 Choosing the AI provider

The GenAI agents (debate + arbitrator + daily brief) default to **DeepSeek**. Two ways to switch:

- **In `.env`** (applies on next start): set `NEXUS_LLM_PROVIDER=openai` (or `gemini` / `local`) and the matching API key.
- **Live from the dashboard**: *Risk tab → Trade Settings → "Proveedor de IA"* dropdown — changes the provider without a restart.

All three cloud providers use OpenAI-compatible endpoints, so switching needs no extra dependencies. `local` targets an Ollama server (`NEXUS_LOCAL_LLM_URL`).

---

## 🩺 Troubleshooting

| Symptom | Cause → Fix |
|---------|-------------|
| Pre-flight `BLOQUEADO` · `Error 10061 connecting to 127.0.0.1:6379` | Redis isn't running → start Docker Desktop, then the `nexus-redis` container (Step 5). |
| `MT5 initialize failed: [-6] Authorization failed` | `.env` login/server don't match the account the terminal is logged into → align them (Step 4). |
| `MT5 initialize failed: [-10001]` | MT5 terminal not running / not logged in → open it and log into the demo account. |
| Pre-flight warns *AutoTrading disabled* | Click the green **Algo Trading** button in MT5. |
| `nexus-ml` health flaps to `000` for a bit after boot | Normal — it's training the bootstrap ensemble (CPU-heavy). It recovers once training finishes. |
| Dashboard `http://localhost:3100` slow/blank on first hit | Next.js dev compiles routes on demand; reload after a few seconds. |
| A service's port is already in use | Something else is on `9000–9008` or `3100` → stop it, or change ports in `.env`. |
| No GenAI decisions, agents idle | No/invalid API key for `NEXUS_LLM_PROVIDER` → the system still trades on the quant ensemble; add a key to enable the agents. |

---

## 🗂️ What runs where

| Service | Port | Role |
|---------|------|------|
| nexus-api | 9000 | gateway / BFF + WebSockets |
| nexus-data | 9001 | MT5 ingestion (ticks / OHLCV) |
| nexus-features | 9002 | multi-timeframe + cross-asset features |
| nexus-ml | 9003 | per-symbol ensemble + training |
| nexus-agents | 9004 | LangGraph agents + research |
| nexus-news | 9005 | economic calendar + sentiment |
| nexus-risk | 9006 | circuit breakers + anti-tilt |
| nexus-executor | 9007 | MT5 execution + kill-switch |
| nexus-monitor | 9008 | health + telemetry |
| dashboard | 3100 | Next.js control room |
| Redis | 6379 | event streams + cache |

---

## 🛡️ Safety & license

- **Demo first.** Never point this at a live account you aren't prepared to lose.
- It's a **research/portfolio** project, licensed **AGPL-3.0** — if you run a modified version as a service, you must publish your source.
- Questions or issues? Open a [GitHub issue](https://github.com/MirandaCR/Nexus-Trading-System-MT5/issues).
