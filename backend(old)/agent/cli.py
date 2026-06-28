"""
CLI entry point for the Orion AI Agent.

Kullanım:
    cd backend && python -m agent.cli
    python -m agent.cli --diag
    python -m agent.cli --diag --no-bind-tools
    python -m agent.cli --once "Merhaba"

Teşhis (graph [llm_diag] logları için):
    --diag              ORION_LLM_DIAG=true
    --no-bind-tools / --fast   ORION_LLM_NO_BIND_TOOLS=true (tool şeması gönderilmez, TTFT düşer)
    --debug-payload     agent/debug: devasa JSON payload logları (varsayılan: kapalı)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
import time


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Orion AI Agent — LangGraph + Gemini (interaktif veya tek mesaj)")
    p.add_argument(
        "--diag",
        action="store_true",
        help="LLM teşhis logları: get_model_ms / ainvoke_ms (ORION_LLM_DIAG=true)",
    )
    p.add_argument(
        "--no-bind-tools",
        action="store_true",
        help="bind_tools atlanır; sadece gecikme A/B testi (ORION_LLM_NO_BIND_TOOLS=true)",
    )
    p.add_argument(
        "--fast",
        action="store_true",
        help="--no-bind-tools ile aynı: tool şeması yok, daha düşük TTFT",
    )
    p.add_argument(
        "--debug-payload",
        action="store_true",
        help="LLM'e giden/gelen devasa JSON debug (config'teki debug ile birleşir)",
    )
    p.add_argument(
        "--once",
        type=str,
        default=None,
        metavar="MESAJ",
        help="Tek mesaj gönder ve çık (hızlı test)",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="DEBUG logları açık (çok gürültülü)",
    )
    return p.parse_args()


def _apply_env_from_args(args: argparse.Namespace) -> None:
    if args.diag:
        os.environ["ORION_LLM_DIAG"] = "true"
    if args.no_bind_tools or args.fast:
        os.environ["ORION_LLM_NO_BIND_TOOLS"] = "true"


async def _run_cli(*, once: str | None, verbose: bool, debug_payload: bool) -> None:
    from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage

    from log import configure_system_log
    from log.settings_models import LogLevels
    from agent.debug import set_cli_debug_mode
    from agent.facade import stream_request
    from agent.model import get_active_tools
    from config.config_manager import get_config_manager

    # Varsayılan: devasa [PAYLOAD] logları kapalı (TTFT ölçümünü kirletmesin).
    set_cli_debug_mode(True if debug_payload else False)

    configure_system_log(
        levels=LogLevels(
            DEBUG=bool(verbose),
            INFO=True,
            WARNING=True,
            ERROR=True,
            STREAM=bool(verbose),
        )
    )

    def _get_system_prompt() -> SystemMessage:
        cfg = get_config_manager().get_config()
        active = getattr(cfg, "active_agent", None)
        if active and getattr(cfg, "agents", None):
            agent_profile = cfg.agents.get(active)
            if agent_profile and getattr(agent_profile, "system_prompt", None):
                return SystemMessage(content=agent_profile.system_prompt)
        return SystemMessage(content=cfg.system_prompt)

    print("Orion AI Agent hazır.")
    _cfg = get_config_manager().get_config()
    _active_name = getattr(_cfg, "active_agent", None)
    _n_tools = len(get_active_tools(_cfg, _active_name))
    _bind_cfg = bool(getattr(_cfg, "enable_tool_binding", True))
    print(
        f"  [config] active_agent={_active_name}  enable_tool_binding={_bind_cfg}  etkin_tool_sayısı={_n_tools}  "
        "(çok tool + bağlama → TTFT artar; ai_config.json veya --fast)"
    )
    if os.environ.get("ORION_LLM_DIAG") == "true":
        print("  [diag] ORION_LLM_DIAG=1  → loglarda [llm_diag] satırlarına bakın.")
    if os.environ.get("ORION_LLM_NO_BIND_TOOLS") == "true":
        print("  [diag] ORION_LLM_NO_BIND_TOOLS=1  → tool bağlama kapalı (sadece test).")
    print("Komutlar: 'quit' çıkış\n")

    history: list = [_get_system_prompt()]

    async def _one_turn(user_input: str) -> None:
        nonlocal history
        history.append(HumanMessage(content=user_input))

        _t_start = time.monotonic()
        _first_token_at: float | None = None
        _stop_spinner = threading.Event()

        def _spinner() -> None:
            frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            i = 0
            while not _stop_spinner.is_set():
                elapsed = time.monotonic() - _t_start
                ttft = ""
                if _first_token_at is not None:
                    ttft = f"  TTFT {_first_token_at - _t_start:.2f}s"
                print(f"\r  ⏳ {frames[i % len(frames)]}  {elapsed:.1f}s{ttft}", end="", flush=True)
                i += 1
                time.sleep(0.1)

        _spin_thread = threading.Thread(target=_spinner, daemon=True)
        _spin_thread.start()
        _spinner_alive = True

        def _stop_once() -> None:
            nonlocal _spinner_alive
            if _spinner_alive:
                _spinner_alive = False
                _stop_spinner.set()
                _spin_thread.join()

        final_messages: list = []

        async def _stream_reply() -> None:
            nonlocal final_messages, _first_token_at
            try:
                cfg = get_config_manager().get_config()
                last_state: dict = {}
                async for stream_type, data in stream_request(
                    messages=history,
                    active_agent_name=getattr(cfg, "active_agent", None),
                ):
                    if stream_type == "values":
                        last_state = data if isinstance(data, dict) else {}
                        continue

                    message, _metadata = data
                    if isinstance(message, AIMessageChunk):
                        if _first_token_at is None:
                            _first_token_at = time.monotonic()
                        _stop_once()

                final_messages = list(last_state.get("messages", []))
            except ValueError as exc:
                _stop_once()
                print(f"\n  ⚠️  {exc}\n", flush=True)

        await _stream_reply()
        _stop_once()

        elapsed_total = time.monotonic() - _t_start
        ttft_str = ""
        if _first_token_at is not None:
            ttft_str = f"  İlk token (TTFT): {_first_token_at - _t_start:.2f}s  |"
        print(f"\n  ⏱  {ttft_str}  Toplam: {elapsed_total:.2f}s\n")

        if final_messages:
            history = final_messages
            if not history or not isinstance(history[0], SystemMessage):
                history = [_get_system_prompt()] + history
            else:
                history[0] = _get_system_prompt()

    if once is not None:
        await _one_turn(once.strip())
        return

    while True:
        try:
            user_input = input("Sen: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGörüşürüz!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("Görüşürüz!")
            break

        await _one_turn(user_input)


def main() -> None:
    args = _parse_args()
    _apply_env_from_args(args)

    # agent → graph → ORION_* env buradan sonra import edilmeli
    try:
        asyncio.run(
            _run_cli(
                once=args.once,
                verbose=args.verbose,
                debug_payload=args.debug_payload,
            )
        )
    except KeyboardInterrupt:
        print("\nGörüşürüz!", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
