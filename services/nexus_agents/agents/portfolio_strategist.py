"""
A12 — El Estratega: Portfolio Strategist agent.

Operates in two modes:
  - Reactive mode: after each trade close — analyses the trade
  - Reflective mode: every 4 hours — reviews portfolio performance

Tracks win rate, profit factor, Sharpe ratio, max drawdown.
Performs anti-tilt detection, model drift checks, and strategy adjustments.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import structlog

from shared.redis_client import RedisClient

from ..state import AgentState
from ..tools.market_tools import query_duckdb
from ..tools.risk_tools import check_exposure
from .base_agent import BaseAgent

logger = structlog.get_logger(__name__)


class PortfolioStrategistAgent(BaseAgent):
    def __init__(self, *, redis_client: RedisClient | None = None) -> None:
        super().__init__("A12", "Portfolio Strategist", redis_client=redis_client)

    async def invoke(self, state: AgentState) -> AgentState:
        symbol = state["symbol"]

        performance = await self._gather_performance()
        tilt_assessment = self._assess_tilt(performance)
        drift_check = self._check_model_drift(performance)

        context_for_llm = (
            f"=== PORTFOLIO REVIEW ===\n"
            f"Symbol just evaluated: {symbol}\n"
            f"Current verdict: {state.get('verdict', 'N/A')}\n"
            f"Conviction: {state.get('verdict_conviction', 0)}\n"
            f"Order placed: {json.dumps(state.get('order'), default=str)}\n\n"
            f"=== PERFORMANCE METRICS ===\n"
            f"Total trades: {performance.get('total_trades', 0)}\n"
            f"Win rate: {performance.get('win_rate', 0):.1f}%\n"
            f"Profit factor: {performance.get('profit_factor', 0):.2f}\n"
            f"Sharpe ratio: {performance.get('sharpe_ratio', 0):.2f}\n"
            f"Max drawdown: {performance.get('max_drawdown', 0):.1f}%\n"
            f"Total PnL: ${performance.get('total_pnl', 0):.2f}\n"
            f"Avg win: ${performance.get('avg_win', 0):.2f}\n"
            f"Avg loss: ${performance.get('avg_loss', 0):.2f}\n"
            f"Consecutive losses: {performance.get('consecutive_losses', 0)}\n"
            f"Today's PnL: ${performance.get('today_pnl', 0):.2f}\n"
            f"Today's trades: {performance.get('today_trades', 0)}\n\n"
            f"=== TILT ASSESSMENT ===\n"
            f"Status: {tilt_assessment['status']}\n"
            f"Reasoning: {tilt_assessment['reasoning']}\n\n"
            f"=== MODEL DRIFT CHECK ===\n"
            f"Status: {drift_check['status']}\n"
            f"Details: {drift_check['details']}\n\n"
            f"=== RECENT TRADE ANALYSIS ===\n"
            f"{json.dumps(performance.get('recent_trades', [])[:5], default=str)}\n\n"
            f"Provide a portfolio review with:\n"
            f"1. Current performance assessment\n"
            f"2. Any strategy adjustments recommended\n"
            f"3. Risk parameter suggestions\n"
            f"4. Confidence in current approach (0-100)"
        )

        try:
            llm = self._get_llm(fast=True)
            prompt = self._load_prompt("portfolio_strategist")
            chain = prompt | llm
            response = await self._invoke_with_budget(chain, {"input": context_for_llm})
            review = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            review = (
                f"[Auto-review]\n"
                f"Win rate: {performance.get('win_rate', 0):.1f}%\n"
                f"PF: {performance.get('profit_factor', 0):.2f}\n"
                f"Tilt: {tilt_assessment['status']}\n"
                f"Drift: {drift_check['status']}"
            )
            state["errors"].append(f"A12: LLM call failed: {exc}")

        state["messages"].append({
            "agent_id": self.agent_id,
            "agent_name": self.name,
            "type": "portfolio_review",
            "content": review[:500],
            "performance": performance,
            "tilt": tilt_assessment,
            "drift": drift_check,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        await self._publish_message(
            f"Portfolio review: WR={performance.get('win_rate', 0):.0f}%, "
            f"PF={performance.get('profit_factor', 0):.1f}, "
            f"Tilt={tilt_assessment['status']}",
            metadata={
                "performance": performance,
                "tilt": tilt_assessment["status"],
                "drift": drift_check["status"],
            },
            state=state,
        )
        await self._log_reasoning(review, state=state)

        return state

    async def _gather_performance(self) -> dict[str, Any]:
        """Collect performance metrics from DuckDB and Redis."""
        performance: dict[str, Any] = {
            "total_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown": 0.0,
            "total_pnl": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "consecutive_losses": 0,
            "today_pnl": 0.0,
            "today_trades": 0,
            "recent_trades": [],
        }

        try:
            trades = await query_duckdb(
                "SELECT * FROM trades ORDER BY closed_at DESC LIMIT 50"
            )
            if trades:
                performance["recent_trades"] = trades[:10]
                performance["total_trades"] = len(trades)

                wins = [t for t in trades if t.get("pnl_usd", 0) > 0]
                losses = [t for t in trades if t.get("pnl_usd", 0) <= 0]

                if trades:
                    performance["win_rate"] = (len(wins) / len(trades)) * 100

                total_wins = sum(t.get("pnl_usd", 0) for t in wins)
                total_losses = abs(sum(t.get("pnl_usd", 0) for t in losses))
                performance["total_pnl"] = total_wins - total_losses

                if total_losses > 0:
                    performance["profit_factor"] = total_wins / total_losses
                elif total_wins > 0:
                    performance["profit_factor"] = 999.0

                if wins:
                    performance["avg_win"] = total_wins / len(wins)
                if losses:
                    performance["avg_loss"] = total_losses / len(losses)

                consec = 0
                for t in trades:
                    if t.get("pnl_usd", 0) <= 0:
                        consec += 1
                    else:
                        break
                performance["consecutive_losses"] = consec

        except Exception:
            pass

        try:
            exposure = await check_exposure()
            performance["max_drawdown"] = exposure.get("weekly_dd_pct", 0.0)
            performance["today_pnl"] = exposure.get("daily_pnl", 0.0)
            performance["consecutive_losses"] = max(
                performance["consecutive_losses"],
                exposure.get("consecutive_losses", 0),
            )
        except Exception:
            pass

        return performance

    def _assess_tilt(self, performance: dict[str, Any]) -> dict[str, str]:
        consecutive = performance.get("consecutive_losses", 0)
        win_rate = performance.get("win_rate", 0)
        max_dd = performance.get("max_drawdown", 0)

        if consecutive >= 5:
            return {
                "status": "TILT",
                "reasoning": f"5+ consecutive losses ({consecutive}). Trading should be paused.",
            }
        if consecutive >= 3:
            return {
                "status": "WARNING",
                "reasoning": f"3+ consecutive losses ({consecutive}). Reduce position sizes.",
            }
        if max_dd >= 6.0:
            return {
                "status": "WARNING",
                "reasoning": f"Drawdown approaching limit ({max_dd:.1f}%). Trade cautiously.",
            }
        if win_rate < 35 and performance.get("total_trades", 0) > 10:
            return {
                "status": "WARNING",
                "reasoning": f"Win rate below 35% ({win_rate:.1f}%). Review strategy.",
            }
        return {
            "status": "NORMAL",
            "reasoning": "No tilt indicators detected.",
        }

    def _check_model_drift(self, performance: dict[str, Any]) -> dict[str, str]:
        recent = performance.get("recent_trades", [])
        if len(recent) < 10:
            return {
                "status": "INSUFFICIENT_DATA",
                "details": f"Only {len(recent)} recent trades — need 10+ for drift analysis.",
            }

        recent_10_wr = sum(1 for t in recent[:10] if t.get("pnl_usd", 0) > 0) / 10 * 100
        overall_wr = performance.get("win_rate", 50)

        if abs(recent_10_wr - overall_wr) > 20:
            return {
                "status": "DRIFT_DETECTED",
                "details": (
                    f"Recent 10-trade WR ({recent_10_wr:.0f}%) deviates significantly "
                    f"from overall ({overall_wr:.0f}%). Model may need recalibration."
                ),
            }

        recent_pf_wins = sum(t.get("pnl_usd", 0) for t in recent[:10] if t.get("pnl_usd", 0) > 0)
        recent_pf_losses = abs(sum(t.get("pnl_usd", 0) for t in recent[:10] if t.get("pnl_usd", 0) <= 0))
        recent_pf = recent_pf_wins / recent_pf_losses if recent_pf_losses > 0 else 999.0

        if recent_pf < 0.8:
            return {
                "status": "DRIFT_WARNING",
                "details": f"Recent profit factor ({recent_pf:.2f}) is below threshold.",
            }

        return {
            "status": "STABLE",
            "details": "No significant model drift detected.",
        }
