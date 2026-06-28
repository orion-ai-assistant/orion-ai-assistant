"""
LangGraph graph tanımı: state, node'lar, kenarlar ve derleme.
Yeni node eklemek: fonksiyon yaz → _workflow'a ekle.
"""
from __future__ import annotations

import time
from typing import Annotated

from langchain_core.messages import AIMessageChunk, SystemMessage, ToolMessage, message_chunk_to_message
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict
from config.config_manager import get_config_manager
from config.settings.ai import ORION_LLM_DIAG
from log import Logger

from .model import get_active_tools, get_llm_diag_context, get_model
from .debug import debug_provider_payload, debug_provider_response, is_debug_mode

logger = Logger(__file__)

# Ensure config manager (and background watcher) is initialized at process startup,
# not only on first user request.
try:
    get_config_manager()
except Exception as e:
    logger.warning(lambda: f"Config manager eager init failed: {e}")

# ── Durum ──────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


# ── Yardımcılar ────────────────────────────────────────────────────────────


def _strip_extras_from_content(content):
    """Remove provider-specific extras (signature vs) to keep logs/json clean.

    Ayrıca Gemini'nin döndürdüğü karışık içeriklerde (dict text + plain string)
    düz string parçayı bir önceki text bloğuna ekleyerek log çıktısının daha
    okunaklı olmasını sağlarız. Modelin ham çıktısını değiştirmiyoruz; sadece
    log görünümünü birleştiriyoruz.
    """
    if not isinstance(content, list):
        return content

    cleaned = []
    for block in content:
        if isinstance(block, dict):
            block_copy = {k: v for k, v in block.items() if k not in {"extras", "index"}}
            cleaned.append(block_copy)
        else:
            cleaned.append(block)

    # Log okunabilirliği: ardışık text + string durumunda string'i text'e ekle
    merged: list = []
    for blk in cleaned:
        if isinstance(blk, str) and merged:
            prev = merged[-1]
            if isinstance(prev, dict) and prev.get("type") == "text" and isinstance(prev.get("text"), str):
                prev["text"] = prev.get("text", "") + blk
                continue
        merged.append(blk)

    return merged


def _sanitize_message_for_log(message):
    """Build a log-friendly object without mutating original message."""
    try:
        content = getattr(message, "content", None)
        return {
            "type": getattr(message, "type", None),
            "id": getattr(message, "id", None),
            "content": _strip_extras_from_content(content),
            "additional_kwargs": getattr(message, "additional_kwargs", {}),
        }
    except Exception:
        return message


# ── Node'lar ───────────────────────────────────────────────────────────────


def _get_current_system_prompt(active_agent_name: str | None = None):
    """Aktif agent'in system_prompt'unu, yoksa global config sistem promptunu döner."""
    cfg = get_config_manager().get_config()
    agent_name = active_agent_name or getattr(cfg, "active_agent", None)

    if agent_name and getattr(cfg, "agents", None):
        agent = cfg.agents.get(agent_name)
        if agent and getattr(agent, "system_prompt", None):
            return SystemMessage(content=agent.system_prompt)

    # Asıl varsayılan strategy: global config.system_prompt kullan
    return SystemMessage(content=cfg.system_prompt)


def _build_provider_debug_payload(llm, send_messages):
    bound_model = getattr(llm, "bound", llm)
    bound_kwargs = getattr(llm, "kwargs", {})

    if hasattr(bound_model, "_get_request_payload"):
        normalized_messages = bound_model._convert_input(send_messages).to_messages()
        return bound_model._get_request_payload(normalized_messages, stream=True, **bound_kwargs)

    if hasattr(bound_model, "_prepare_request"):
        normalized_messages = bound_model._convert_input(send_messages).to_messages()
        return bound_model._prepare_request(normalized_messages, **bound_kwargs)

    return send_messages

def _approx_messages_chars(msgs: list) -> int:
    total = 0
    for m in msgs:
        c = getattr(m, "content", "")
        if isinstance(c, str):
            total += len(c)
        elif isinstance(c, list):
            for block in c:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    total += len(block["text"])
                elif isinstance(block, str):
                    total += len(block)
        else:
            total += len(str(c))
    return total


def _extract_request_id(config: RunnableConfig | None) -> str | None:
    if not isinstance(config, dict):
        return None

    metadata = config.get("metadata")
    if isinstance(metadata, dict):
        request_id = metadata.get("request_id")
        if isinstance(request_id, str) and request_id:
            return request_id

    configurable = config.get("configurable")
    if isinstance(configurable, dict):
        request_id = configurable.get("request_id")
        if isinstance(request_id, str) and request_id:
            return request_id

    return None


def _extract_active_agent_name(config: RunnableConfig | None) -> str | None:
    if not isinstance(config, dict):
        return None

    configurable = config.get("configurable")
    if isinstance(configurable, dict):
        agent_name = configurable.get("active_agent_name")
        if isinstance(agent_name, str) and agent_name:
            return agent_name

    metadata = config.get("metadata")
    if isinstance(metadata, dict):
        agent_name = metadata.get("active_agent_name")
        if isinstance(agent_name, str) and agent_name:
            return agent_name

    return None


async def call_model(state: AgentState, config: RunnableConfig) -> dict:
    messages = state["messages"]
    active_agent_name = _extract_active_agent_name(config)
    # Konfigüre edilmiş aktif agent prompt'unu veya global promptu kullan.
    current_prompt = _get_current_system_prompt(active_agent_name=active_agent_name)
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [current_prompt] + list(messages)
    else:
        messages[0] = current_prompt

    diag_ctx = get_llm_diag_context(active_agent_name) if ORION_LLM_DIAG else {}
    t0 = time.perf_counter()
    llm = get_model(active_agent_name)
    t_build = time.perf_counter() - t0
    send_messages = messages

    if is_debug_mode():
        provider_payload = _build_provider_debug_payload(llm, send_messages)
        debug_provider_payload(provider_payload)

    # Gerçek token akışı için modeli astream ile tüketip chunk'ları tek AIMessage'e
    # birleştiriyoruz. Böylece ilk token gecikmesi (TTFT) tam yanıt beklemeden çıkabilir.
    request_id = _extract_request_id(config)
    t1 = time.perf_counter()
    merged_chunk: AIMessageChunk | None = None
    first_chunk_ms: float | None = None
    chunk_count = 0

    async for chunk in llm.astream(send_messages, config=config):
        if not isinstance(chunk, AIMessageChunk):
            continue
        chunk_count += 1
        if first_chunk_ms is None:
            first_chunk_ms = (time.perf_counter() - t1) * 1000.0
        if merged_chunk is None:
            merged_chunk = chunk
        else:
            merged_chunk += chunk

    if merged_chunk is None:
        response = await llm.ainvoke(send_messages, config=config)
    else:
        response = message_chunk_to_message(merged_chunk)

    t_invoke = time.perf_counter() - t1

    if ORION_LLM_DIAG:
        msg_chars = _approx_messages_chars(send_messages)
        logger.info(
            "[llm_diag] "
            f"request_id={request_id or '-'} "
            f"provider={diag_ctx.get('provider')} "
            f"n_tools={diag_ctx.get('n_tools')} "
            f"thinking={diag_ctx.get('thinking_enabled')} "
            f"no_bind_tools={diag_ctx.get('no_bind_tools')} "
            f"enable_tool_binding={diag_ctx.get('enable_tool_binding')} "
            f"get_model_ms={t_build * 1000.0:.1f} "
            f"astream_ms={t_invoke * 1000.0:.1f} "
            f"first_chunk_ms={(f'{first_chunk_ms:.1f}' if first_chunk_ms is not None else '-')} "
            f"chunks={chunk_count} "
            f"input_chars={msg_chars}"
        )

    if is_debug_mode():
        debug_provider_response(_sanitize_message_for_log(response))

    return {"messages": [response]}


def guidance_node(state: AgentState) -> dict:
    """
    Tool çağrısından sonra ajana ek bağlam/yönerge enjekte etmek istersen
    buraya SystemMessage ekle. Şu an boş bırakılmış — ihtiyaç olunca doldur.
    Örnek:
        return {"messages": [SystemMessage(content="Kaynakları mutlaka belirt.")]}
    """
    return {"messages": []}


def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else END


def route_after_tools(state: AgentState) -> str:
    """Tool çağrısından sonra, eğer ajan do_not_reply aracını kullandıysa doğrudan END'e git."""
    for msg in reversed(state["messages"]):
        if getattr(msg, "type", "") == "tool":
            if getattr(msg, "name", "") == "do_not_reply":
                return END
        else:
            break
    return "guidance"


# ── Graf ───────────────────────────────────────────────────────────────────

def build_graph(active_agent_name: str | None = None):
    """Aktif konfige göre graph derler."""
    cfg = get_config_manager().get_config()
    agent_name = active_agent_name or cfg.active_agent

    logger.info(lambda: f"Building graph (agent={agent_name}, last_updated={cfg.last_updated})")

    workflow = StateGraph(AgentState)
    workflow.add_node("agent", call_model)
    workflow.add_node("tools", ToolNode(get_active_tools(cfg, active_agent_name=agent_name)))
    workflow.add_node("guidance", guidance_node)

    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    workflow.add_conditional_edges("tools", route_after_tools, {"guidance": "guidance", END: END})
    workflow.add_edge("guidance", "agent")

    return workflow.compile()


def get_graph(active_agent_name: str | None = None):
    cfg = get_config_manager().get_config()
    agent_name = active_agent_name or cfg.active_agent
    last_updated = getattr(cfg, "last_updated", None)
    cache_key = f"{agent_name}:{last_updated}"

    if not hasattr(get_graph, "_cache"):
        get_graph._cache = {}

    graph_instance = get_graph._cache.get(cache_key)
    if graph_instance is not None:
        logger.info(lambda: f"get_graph cache hit (agent={agent_name}, last_updated={last_updated})")
        return graph_instance

    logger.info(lambda: f"get_graph cache miss -> rebuild graph (agent={agent_name}, last_updated={last_updated})")
    graph_instance = build_graph(agent_name)
    get_graph._cache.clear()
    get_graph._cache[cache_key] = graph_instance

    logger.info(lambda: f"get_graph stored graph in cache (agent={agent_name}, last_updated={last_updated})")
    return graph_instance


def clear_graph_cache() -> None:
    """Clear all cached compiled graphs."""
    if hasattr(get_graph, "_cache"):
        get_graph._cache.clear()


def warm_graph_cache(active_agent_names: list[str] | None = None) -> None:
    """Prebuild graphs so the next user request does not pay compile cost."""
    cfg = get_config_manager().get_config()
    agent_names = active_agent_names or list((cfg.agents or {}).keys())
    clear_graph_cache()
    for agent_name in agent_names:
        try:
            get_graph(agent_name)
        except Exception as exc:
            logger.warning(lambda: f"Graph warm-up failed for agent={agent_name}: {exc}")

