"""
Nexus Agents — Abstract base class for all trading agents.

Provides:
  - LLM instantiation (cloud via DeepSeek, local via Ollama)
  - Prompt loading from YAML files
  - Message publishing to Redis Streams
  - Structured reasoning logs
  - Error handling wrapper
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
import yaml
from langchain_core.prompts import ChatPromptTemplate

from shared.constants import AGENT_COLORS, AGENT_NAMES, AgentID, RedisStream
from shared.events import AgentAnalysisEvent
from shared.models import AgentMessage
from shared.redis_client import RedisClient

from ..state import AgentState

logger = structlog.get_logger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

AGENT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "agents.yaml"


def _load_agent_configs() -> dict[str, Any]:
    if AGENT_CONFIG_PATH.exists():
        with open(AGENT_CONFIG_PATH) as f:
            return yaml.safe_load(f).get("agents", {})
    return {}


_AGENT_CONFIGS = _load_agent_configs()


class BaseAgent(ABC):
    """Abstract base for every Nexus trading agent (A1-A12)."""

    def __init__(
        self,
        agent_id: str,
        name: str,
        *,
        redis_client: RedisClient | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.name = name
        self.redis = redis_client
        self.config = config or _AGENT_CONFIGS.get(agent_id, {})
        self.color = AGENT_COLORS.get(agent_id, "#FFFFFF")
        self.nickname = self.config.get("nickname", name)
        self._prompt_template: ChatPromptTemplate | None = None

    # ── Abstract ──────────────────────────────────────────────────────────

    @abstractmethod
    async def invoke(self, state: AgentState) -> AgentState:
        """Run the agent logic and return the updated state."""
        ...

    # ── LLM Factory ───────────────────────────────────────────────────────

    def _get_llm(self, *, temperature: float | None = None, max_tokens: int | None = None, fast: bool = False):
        """Return a LangChain chat model based on config (cloud or local)."""
        provider = os.getenv("NEXUS_LLM_PROVIDER", "cloud")
        temp = temperature or self.config.get("temperature", 0.3)
        tokens = max_tokens or self.config.get("max_tokens", 800)

        if provider == "local":
            from langchain_openai import ChatOpenAI

            local_url = os.getenv("NEXUS_LOCAL_LLM_URL", "http://localhost:11434/v1")
            model = self.config.get("model_local", "llama3.3:70b")
            return ChatOpenAI(
                base_url=local_url,
                api_key="ollama",
                model=model,
                temperature=temp,
                max_tokens=tokens,
                request_timeout=30,
            )

        from langchain_openai import ChatOpenAI

        # DeepSeek (OpenAI-compatible)
        model = self.config.get("model_cloud", "deepseek-chat")
        return ChatOpenAI(
            base_url="https://api.deepseek.com/v1",
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            model=model,
            temperature=temp,
            max_tokens=tokens,
            request_timeout=30,
        )

    async def _invoke_with_budget(self, chain, input_dict: dict[str, Any]) -> Any:
        """Invoke a LangChain chain with daily budget enforcement.

        - Checks if budget is exhausted before calling.
        - Reduces max_tokens when near the daily limit.
        - Records token usage after the call.
        Returns None if budget is fully spent (callers must handle this case).
        """
        from ..brain_budget import budget_status, record_usage

        redis = self.redis
        if redis is None:
            # No Redis client — skip budget tracking, call directly
            return await chain.ainvoke(input_dict)

        try:
            status = await budget_status(redis.r)
        except Exception:
            return await chain.ainvoke(input_dict)

        if not status["allowed"]:
            logger.warning(
                "brain_budget.exhausted agent=%s spent=$%.4f limit=$%.2f",
                self.agent_id, status["cost_usd"], status["daily_limit_usd"],
            )
            return None

        result = await chain.ainvoke(input_dict)

        # Record usage from response metadata (best-effort)
        try:
            usage_meta = getattr(result, "response_metadata", {}) or {}
            token_usage = usage_meta.get("token_usage") or {}
            input_t = int(token_usage.get("prompt_tokens", 0) or 0)
            output_t = int(token_usage.get("completion_tokens", 0) or 0)
            if input_t == 0 and output_t == 0:
                # Fallback: rough character-based estimate
                content = getattr(result, "content", "") or ""
                input_est = len(str(input_dict.get("input", ""))) // 4
                output_est = len(content) // 4
                await record_usage(redis.r, input_est, output_est)
            else:
                await record_usage(redis.r, input_t, output_t)
        except Exception:
            pass

        return result

    # ── Prompt Loading ────────────────────────────────────────────────────

    def _load_prompt(self, prompt_name: str | None = None) -> ChatPromptTemplate:
        """Load a system prompt from the YAML prompts directory."""
        if self._prompt_template is not None:
            return self._prompt_template

        fname = prompt_name or self.name.lower().replace(" ", "_")
        yaml_path = PROMPTS_DIR / f"{fname}.yaml"

        if yaml_path.exists():
            with open(yaml_path) as f:
                data = yaml.safe_load(f)
            system_prompt = data.get("system_prompt", "")
        else:
            system_prompt = (
                f"You are {self.nickname}, the {self.name} agent in the Nexus Trader system. "
                f"Role: {self.config.get('role', 'Analyze and provide trading insights.')}"
            )

        self._prompt_template = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", "{input}"),
        ])
        return self._prompt_template

    # ── Publishing ────────────────────────────────────────────────────────

    async def _publish_message(
        self,
        content: str,
        metadata: dict[str, Any] | None = None,
        *,
        state: AgentState | None = None,
    ) -> None:
        """Publish an AgentMessage to Redis for dashboard consumption."""
        if not self.redis:
            return

        msg = AgentMessage(
            agent_id=self.agent_id,
            agent_name=self.name,
            agent_color=self.color,
            message_type="analysis",
            content=content,
            metadata=metadata or {},
        )

        event = AgentAnalysisEvent(
            source=f"agent.{self.agent_id}",
            correlation_id=state.get("cycle_id", "") if state else "",
            agent_id=self.agent_id,
            agent_name=self.name,
            symbol=state.get("symbol", "") if state else "",
            analysis=content,
            confidence=0.0,
            metadata=msg.model_dump(),
        )
        try:
            await self.redis.publish_event(RedisStream.AGENT_ANALYSIS, event)
        except Exception:
            await logger.awarning("agent.publish_failed", agent=self.agent_id)

    # ── Logging ───────────────────────────────────────────────────────────

    async def _log_reasoning(self, reasoning: str, *, state: AgentState | None = None) -> None:
        """Write structured reasoning to the log."""
        await logger.ainfo(
            "agent.reasoning",
            agent_id=self.agent_id,
            agent_name=self.name,
            cycle_id=state.get("cycle_id", "") if state else "",
            symbol=state.get("symbol", "") if state else "",
            reasoning=reasoning[:500],
        )

    # ── Error Wrapper ─────────────────────────────────────────────────────

    async def safe_invoke(self, state: AgentState) -> AgentState:
        """Invoke with error handling — failures are logged and recorded in state."""
        try:
            return await self.invoke(state)
        except Exception as exc:
            error_msg = f"[{self.agent_id}:{self.name}] {exc}"
            state["errors"].append(error_msg)
            state["messages"].append({
                "agent_id": self.agent_id,
                "type": "error",
                "content": error_msg,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            await logger.aexception(
                "agent.invoke_error",
                agent_id=self.agent_id,
                agent_name=self.name,
            )
            return state
