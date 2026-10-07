# Classifier eval (Phase 2)

Acceptance: ≥85% agreement with hand labels on on-topic/off-topic, on a sample of
**50 real collected posts** per run, and the same eval re-run against a second
model/provider by changing env vars only.

1. Collect some real data, then export a random sample to label:

       uv run python -m tests.eval.export_sample --topic pa-energy -n 50 \
           > tests/eval/labels_pa-energy.jsonl

2. Open the file and set `"on_topic": true|false` on every line (leave `note` for
   anything ambiguous). Don't look at the model's output first.

3. Run the eval with the default model (from `.env`), then with another one:

       uv run python -m tests.eval.run_eval tests/eval/labels_pa-energy.jsonl
       LLM_BASE_URL=https://api.openai.com/v1 LLM_API_KEY=sk-... LLM_MODEL=gpt-5-mini \
           uv run python -m tests.eval.run_eval tests/eval/labels_pa-energy.jsonl

   Each run prints agreement, precision/recall for "on topic", lists disagreements, and
   saves a JSON report under `tests/eval/results/`. Record the headline numbers in
   PLAN.md §15.

`labels_synthetic.jsonl` is a small hand-written set that exercises the harness and the
prompt on obvious cases (keyword collisions, out-of-state, passing mentions). It is **not**
the acceptance sample.
