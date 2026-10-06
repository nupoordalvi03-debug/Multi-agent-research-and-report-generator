"""Check configuration and connectivity:   cd backend && python -m app.check_live [--search | --llm]
Prints where each key comes from (never the key itself), then makes one small real request per service.
LLM and TinyFish are separate services and can be checked independently."""
import asyncio
import logging
import sys

from .config import settings
from .services.diagnostics import check_search
from .services.factory import build_llm, log_config


async def run(target: str) -> bool:
    ok = True
    if target in ("all", "search"):
        r = await check_search(settings)
        print(f"\n[{'PASS' if r['ok'] else ('SKIP' if r['status'] == 'not_configured' else 'FAIL')}] Search (TinyFish): {r['message']}")
        ok &= r["ok"]
    if target in ("all", "llm"):
        st = settings.llm_status()
        llm = build_llm(settings)
        if llm is None:
            print(f"\n[SKIP] LLM: NOT CONFIGURED: {st['problem']}  (no request was sent)")
            ok = False
        else:
            h = await llm.health_check()
            print(f"\n[{'PASS' if h['ok'] else 'FAIL'}] LLM ({st['provider']}/{st['model']}): {h['title']} {'' if h['ok'] else h['message']}")
            if not h["ok"]:
                print(f"       {h['hint']}")
            ok &= h["ok"]
    return ok


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    target = "search" if "--search" in sys.argv else "llm" if "--llm" in sys.argv else "all"
    log_config(logging.getLogger("config"), settings)
    ok = asyncio.run(run(target))
    print("\nReady for live research." if ok and target == "all" else "" if ok else
          "\nNot ready: fix the items above. Autonomous research stays blocked until the LLM check passes.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
