# Evaluation Nodes — Agent Navigation

## Purpose

Evaluation nodes assess synthesis quality against retrieved sources before any
subsequent review or DEFINER-gated promotion decision.

## Architecture Constraints

- Import only from `aip.foundation` and the standard library.
- Receive model dispatchers and stores through injected protocols; never import
  adapter implementations.
- CI fixtures must remain marked so production promotion paths reject them.

## Contracts

- `evaluate_faithfulness()` returns `FaithfulnessResult` with
  `faithfulness_score`, `context_coverage`, `hallucination_flags`, and
  `ci_fixture`.
- Both evaluation consumers require their complete documented JSON schemas;
  incomplete JSON is not a successful production evaluation.
- Explicit CI fixtures return deterministic scores with `ci_fixture=True`.

## Data Flows (In / Out)

- Model resolver response → `evaluate_faithfulness()` → canonical and L3a
  pipelines via `FaithfulnessResult`.
- Retrieved `Chunk` values provide the source-grounding context.

## Known Gotchas

- The shared `evaluation` slot can return ARISTOTLE's JSON fixture in CI. Its
  `[CI-FIXTURE]` feedback marker must be recognized before faithfulness or
  coherence-schema validation.

## Last Cycle

- Created for the CI fixture schema-discrimination repair.

## Key Files

- `faithfulness.py` — source-grounding evaluation and CI fixture detection.
- `domain_coherence.py` — domain-level quality evaluation.
- `adversarial_eval.py` — adversarial quality checks.

## Work Guidance

- Never treat missing faithfulness fields as zeros in a real evaluation.
- Keep CI fixtures explicit and non-promotable outside CI.

## How to Test

```bash
CI=true uv run pytest tests/test_vector_pipeline_integration.py -q
uv run pytest tests/test_evaluation_pipeline.py -q
```
