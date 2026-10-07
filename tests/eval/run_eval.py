"""Measure classifier agreement with hand labels.

    python -m tests.eval.run_eval LABELS.jsonl [--batch-size 20]

The model/provider come from LLM_BASE_URL / LLM_API_KEY / LLM_MODEL like production.
Calls are logged to llm_calls with purpose='eval' (needs DATABASE_URL).
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.llm import LLM
from app.pipeline.classify import Candidate, Classifier
from app.schemas import Topic
from app.topics import load_topic_files

RESULTS = Path(__file__).parent / "results"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("labels", type=Path)
    p.add_argument("--batch-size", type=int, default=20)
    a = p.parse_args()

    rows = [json.loads(line) for line in a.labels.read_text().splitlines() if line.strip()]
    unlabeled = [r for r in rows if r.get("on_topic") is None]
    if unlabeled:
        sys.exit(f"{len(unlabeled)} rows have no on_topic label yet")
    configs = {c.slug: c for c in load_topic_files()}
    llm = LLM()
    clf = Classifier(llm, batch_size=a.batch_size, purpose="eval")

    predictions: dict[int, dict] = {}
    for slug in sorted({r["topic"] for r in rows}):
        cfg = configs[slug]
        topic = Topic(
            id=0,
            slug=slug,
            name=cfg.name,
            description=cfg.description,
            window_days=cfg.window_days,
            active=True,
            config=cfg,
        )
        items = [(i, r) for i, r in enumerate(rows) if r["topic"] == slug]
        cands = [
            Candidate(
                post_id=i,
                text=r["text"],
                platform=r.get("platform", "x"),
                kind=r.get("kind", "post"),
                author="",
            )
            for i, r in items
        ]
        # Evaluate the model itself: no prefilter/dedupe shortcuts.
        for start in range(0, len(cands), a.batch_size):
            for c in clf.classify_batch(topic, cands[start : start + a.batch_size]):
                predictions[c.post_id] = c.model_dump()

    tp = fp = tn = fn = 0
    disagreements = []
    for i, r in enumerate(rows):
        pred = predictions.get(i)
        if pred is None:
            disagreements.append({"row": i, "text": r["text"][:200], "error": "no prediction"})
            fn += r["on_topic"]
            fp += not r["on_topic"]
            continue
        p_on = pred["on_topic"] and pred["confidence"] >= llm.settings.on_topic_min_confidence
        if p_on and r["on_topic"]:
            tp += 1
        elif p_on:
            fp += 1
        elif r["on_topic"]:
            fn += 1
        else:
            tn += 1
        if p_on != r["on_topic"]:
            disagreements.append(
                {"row": i, "label": r["on_topic"], "pred": pred, "text": r["text"][:200]}
            )
    n = len(rows)
    report = {
        "labels": str(a.labels),
        "model": llm.model,
        "base_url": str(llm.client.base_url),
        "n": n,
        "agreement": (tp + tn) / n if n else 0,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "disagreements": disagreements,
        "run_at": datetime.now(UTC).isoformat(),
    }
    RESULTS.mkdir(exist_ok=True)
    out = (
        RESULTS / f"{a.labels.stem}__{llm.model.replace('/', '_')}__"
        f"{datetime.now(UTC):%Y%m%dT%H%M}.json"
    )
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps({k: v for k, v in report.items() if k != "disagreements"}, indent=2))
    for d in disagreements:
        print(f"  ✗ row {d['row']}: label={d.get('label')} {d['text'][:120]!r}")
    print(f"saved {out}")


if __name__ == "__main__":
    main()
