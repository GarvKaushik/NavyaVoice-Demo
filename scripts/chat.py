"""Text chat with the real agent (same engine as phone calls), for quick testing.

    python -m scripts.chat                      # interactive
    python -m scripts.chat --say "haan boliye" --say "price kya hai?"   # scripted
"""

import argparse
import asyncio
import logging
import sys
import time
import uuid

from agent.dialogue import DialogueEngine
from agent.knowledge import load_knowledge
from agent.tools import CallIdentity
from llm.client import LLMClient

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def show(u) -> None:
    tag = {"filler": " [filler]", "guard_replacement": " [GUARD REPLACED]", "scripted": " [cached]"}.get(u.kind, "")
    print(f"  Priya: {u.text}{tag}")
    for r in u.guard_reasons:
        print(f"         guard: {r}")


async def main(lines, name: str, campaign: str, verbose: bool) -> None:
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    events = []
    identity = CallIdentity(call_id=f"chat_{uuid.uuid4().hex[:8]}", lead_id="LEAD-TEST-001", campaign_id=campaign,
                            phone_number="+919876543210", customer_name=name)
    knowledge = await load_knowledge(campaign)
    llm = LLMClient.realtime()
    engine = DialogueEngine(identity, knowledge, llm, observer_llm=llm,
                            on_event=lambda kind, data: events.append((kind, data)))
    print(f"(knowledge source: {knowledge.source}; model: {llm.endpoints[0].model})\n")
    for u in engine.opening():
        show(u)

    async def turn(text: str) -> bool:
        print(f"\n  You  : {text}")
        t0 = time.monotonic()
        scripted = await engine.pre_turn(text)
        if scripted is not None:
            for u in scripted.utterances:
                show(u)
        else:
            first = None
            async for u in engine.stream_reply():
                first = first or (time.monotonic() - t0) * 1000
                show(u)
            ttft = engine.last_turn_metrics.get("llm_ttft_ms")
            print(f"         [stage {engine.stage.current_stage.value} | first sentence {first:.0f} ms"
                  f"{f' | LLM TTFT {ttft:.0f} ms' if ttft else ''}]")
        for kind, data in events:
            if kind in ("tool_call", "fast_path", "stage_change", "late_intent"):
                brief = {k: v for k, v in data.items() if k != "result"}
                if kind == "tool_call":
                    brief["ok"] = data["result"].get("ok")
                print(f"         <{kind}> {brief}")
        events.clear()
        if engine.pending_action:
            print(f"\n  [call action: {engine.pending_action.kind} -> outcome {engine.pending_action.outcome}]")
            return False
        return True

    if lines:
        for line in lines:
            if not await turn(line):
                break
    else:
        print("\n(type your reply; empty line or Ctrl+C to end)")
        while True:
            try:
                text = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text or not await turn(text):
                break
    await engine.drain_observer()
    s = engine.summary()
    print(f"\nOutcome: {s['outcome']} | stage: {s['stage_reached']} | guard replacements: {s['guard_replacements']} "
          f"| tokens in/out: {s['usage'].input_tokens}/{s['usage'].output_tokens}")
    print(f"Call state: {s['call_state']}")
    await llm.aclose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--say", action="append", help="scripted customer line (repeatable)")
    ap.add_argument("--name", default="Rajesh")
    ap.add_argument("--campaign", default="CAMP-REENGAGE-Q4")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.say, args.name, args.campaign, args.verbose))
