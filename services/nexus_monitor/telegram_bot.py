"""
Nexus Monitor — Telegram notification bot.

Sends formatted trade alerts, error notifications, daily summaries,
and kill-switch alerts to a configured Telegram chat with rate limiting.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_MAX_MESSAGES_PER_MINUTE = 20
_MINUTE_WINDOW = 60.0


class TelegramNotifier:
    """Async Telegram bot with rate limiting and formatted message templates."""

    def __init__(
        self,
        bot_token: str = "",
        chat_id: str = "",
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._bot: Any = None
        self._message_times: list[float] = []
        self._enabled = bool(bot_token and chat_id)

    async def _get_bot(self) -> Any:
        if self._bot is None:
            try:
                from telegram import Bot
                self._bot = Bot(token=self._bot_token)
            except ImportError:
                log.warning("telegram.import_failed", msg="python-telegram-bot not installed")
                self._enabled = False
                return None
        return self._bot

    def _check_rate_limit(self) -> bool:
        now = time.monotonic()
        self._message_times = [t for t in self._message_times if now - t < _MINUTE_WINDOW]
        if len(self._message_times) >= _MAX_MESSAGES_PER_MINUTE:
            log.warning("telegram.rate_limited", count=len(self._message_times))
            return False
        return True

    # ── Core Send ─────────────────────────────────────────────────────────

    async def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        if not self._enabled:
            log.debug("telegram.disabled")
            return False

        if not self._check_rate_limit():
            return False

        bot = await self._get_bot()
        if bot is None:
            return False

        try:
            await bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode=parse_mode,
            )
            self._message_times.append(time.monotonic())
            log.debug("telegram.message_sent", length=len(text))
            return True
        except Exception as exc:
            log.error("telegram.send_failed", error=str(exc))
            return False

    # ── Trade Alerts ──────────────────────────────────────────────────────

    async def alert_new_trade(self, trade_details: dict[str, Any]) -> bool:
        symbol = trade_details.get("symbol", "???")
        side = trade_details.get("side", "???")
        entry = trade_details.get("entry_price", 0)
        sl = trade_details.get("sl", 0)
        tp = trade_details.get("tp", trade_details.get("tp1", 0))
        size = trade_details.get("size", 0)
        conviction = trade_details.get("conviction", 0)

        arrow = "\U0001F7E2" if side.upper() == "BUY" else "\U0001F534"
        text = (
            f"{arrow} *NEW TRADE*\n"
            f"\n"
            f"\U0001F4B1 *{symbol}* | {side.upper()}\n"
            f"\U0001F3AF Entry: `{entry}`\n"
            f"\U0001F6D1 SL: `{sl}`\n"
            f"\U00002705 TP: `{tp}`\n"
            f"\U0001F4E6 Size: `{size}` lots\n"
            f"\U0001F4CA Conviction: `{conviction:.0%}`"
        )
        return await self.send_message(text)

    async def alert_trade_closed(self, trade_result: dict[str, Any]) -> bool:
        symbol = trade_result.get("symbol", "???")
        pnl_pips = trade_result.get("pnl_pips", 0)
        pnl_usd = trade_result.get("pnl_usd", 0)
        rr = trade_result.get("rr_ratio", 0)
        duration = trade_result.get("duration", 0)

        emoji = "\U0001F4B0" if pnl_usd >= 0 else "\U0001F4A8"
        sign = "+" if pnl_usd >= 0 else ""
        text = (
            f"{emoji} *TRADE CLOSED*\n"
            f"\n"
            f"\U0001F4B1 *{symbol}*\n"
            f"\U0001F4B5 P&L: `{sign}{pnl_usd:.2f}` USD (`{sign}{pnl_pips:.1f}` pips)\n"
            f"\U0001F4CA R:R: `{rr:.2f}`\n"
            f"\U000023F1 Duration: `{duration:.0f}` min"
        )
        return await self.send_message(text)

    async def alert_error(self, service: str, error: str) -> bool:
        text = (
            f"\U000026A0\U0000FE0F *SERVICE ERROR*\n"
            f"\n"
            f"\U0001F3E2 Service: `{service}`\n"
            f"\U0000274C Error: `{error[:200]}`"
        )
        return await self.send_message(text)

    async def alert_daily_summary(self, metrics: dict[str, Any]) -> bool:
        total_pnl = metrics.get("total_pnl", 0)
        total_trades = metrics.get("total_trades", 0)
        win_rate = metrics.get("win_rate", 0)
        profit_factor = metrics.get("profit_factor", 0)
        max_dd = metrics.get("max_drawdown", 0)

        sign = "+" if total_pnl >= 0 else ""
        emoji = "\U0001F4C8" if total_pnl >= 0 else "\U0001F4C9"
        text = (
            f"{emoji} *DAILY SUMMARY*\n"
            f"\n"
            f"\U0001F4B5 P&L: `{sign}{total_pnl:.2f}` USD\n"
            f"\U0001F4CA Trades: `{total_trades}`\n"
            f"\U00002705 Win Rate: `{win_rate:.1f}%`\n"
            f"\U0001F4AA Profit Factor: `{profit_factor:.2f}`\n"
            f"\U0001F4C9 Max Drawdown: `{max_dd:.2f}%`"
        )
        return await self.send_message(text)

    async def alert_kill_switch(self, reason: str = "") -> bool:
        text = (
            f"\U0001F6A8\U0001F6A8 *KILL SWITCH ACTIVATED* \U0001F6A8\U0001F6A8\n"
            f"\n"
            f"All trading has been *immediately halted*.\n"
            f"\U0001F4CB Reason: `{reason or 'Manual activation'}`\n"
            f"\n"
            f"_Check the dashboard for details._"
        )
        return await self.send_message(text)
