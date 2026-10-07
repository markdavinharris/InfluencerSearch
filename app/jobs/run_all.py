"""Daily pipeline: collect -> classify -> embed -> score. Run by the Railway cron service.

    python -m app.jobs.run_all [--skip collect,embed,...] [--topic SLUG ...]

Each stage runs even if an earlier one failed (scores computed on whatever was collected and
classified are still worth having); the process exits non-zero if any stage failed or every
collector run errored, so the cron run shows as failed in Railway.
"""

import argparse
import sys
import time
from collections.abc import Callable

from app.config import get_settings
from app.jobs.classify import run_classify
from app.jobs.collect import run_collect
from app.jobs.embed import run_embed
from app.jobs.score import run_score
from app.jobs.sync_topics import sync_topics
from app.log import get_logger, setup_logging

log = get_logger(__name__)

STAGES = ("sync", "collect", "classify", "embed", "score")


def run_all(skip: set[str] | None = None, topics: list[str] | None = None) -> bool:
    skip = skip or set()
    ok = True

    def stage(name: str, fn: Callable[[], object]) -> object:
        nonlocal ok
        if name in skip:
            log.info("stage_skipped", stage=name)
            return None
        t0 = time.monotonic()
        log.info("stage_start", stage=name)
        try:
            result = fn()
        except Exception:
            ok = False
            log.exception("stage_failed", stage=name, seconds=round(time.monotonic() - t0, 1))
            return None
        log.info("stage_done", stage=name, seconds=round(time.monotonic() - t0, 1))
        return result

    stage("sync", sync_topics)

    results = stage("collect", lambda: run_collect(topics))
    if isinstance(results, list):
        errors = [f"{r.platform}/{r.topic_slug}: {r.error}" for r in results if r.error]
        log.info(
            "collect_summary",
            runs=len(results),
            errors=len(errors),
            inserted=sum(r.inserted for r in results),
        )
        for e in errors:
            log.error("collector_error", detail=e)
        if results and len(errors) == len(results):
            ok = False

    finished = stage("classify", lambda: run_classify(topics))
    if finished is False:
        ok = False  # token cap hit: logged by the classify job; scores still run below

    embed_ready = bool(get_settings().embed_api_key)
    if embed_ready:
        stage("embed", run_embed)
    else:
        log.warning("stage_skipped", stage="embed", reason="EMBED_API_KEY not set")

    stage("score", lambda: run_score(topics))
    log.info("run_all_done", ok=ok)
    return ok


def main() -> None:
    setup_logging()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--skip", default="", help=f"comma-separated stages to skip: {STAGES}")
    p.add_argument("--topic", action="append")
    a = p.parse_args()
    skip = {s.strip() for s in a.skip.split(",") if s.strip()}
    if unknown := skip - set(STAGES):
        p.error(f"unknown stages: {sorted(unknown)}")
    sys.exit(0 if run_all(skip, a.topic) else 1)


if __name__ == "__main__":
    main()
