"""Every LLM call site must version its own prompt in its idempotency key.

`docs/project-blueprint/shared-workflow/09-langgraph-operating-model.md`
("Production Controls") requires versioned prompts. `NodeRuntime` and the
model providers cache by `idempotency_key`, so a key that omits the prompt
version replays a cached completion produced by a materially different
prompt after any prompt change -- the change silently does nothing. S03.50
and S07.70 both shipped without it; this test keeps every call site honest.

Scoped to `ModelCompletionRequest(...)` constructions only: an artifact
store's own `idempotency_key` (see each `_put_envelope`) addresses content,
not a prompt, and correctly has no prompt version in it.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src" / "production_optimizer" / "application"

# Each entry: a file holding at least one real model call, and the
# prompt-version constant that file's model calls must key themselves by.
_CALL_SITES = (
    ("a2_handlers/shared.py", "_A2_MODEL_PROMPT_VERSION"),
    ("a3_handlers/shared.py", "_A3_PROMPT_VERSION"),
    (
        "s02_handlers/nodes/s02_30_draft_ordered_phases_and_tasks_via_generator_model_call.py",
        "_PROMPT_VERSION",
    ),
    (
        "s02_handlers/nodes/s02_80_independent_critic_reviews_omissions_and_blast_radius.py",
        "_PROMPT_VERSION",
    ),
    ("s03_agent_loop.py", "_PROMPT_VERSION"),
    ("s07_handlers.py", "_S07_PROMPT_VERSION"),
)


def _model_request_idempotency_keys(source: str) -> list[str]:
    """Every `idempotency_key=` expression passed to a `ModelCompletionRequest`."""

    tree = ast.parse(source)
    keys: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name != "ModelCompletionRequest":
            continue
        for keyword in node.keywords:
            if keyword.arg == "idempotency_key":
                keys.append(ast.unparse(keyword.value))
    return keys


def test_every_llm_call_site_versions_its_prompt_in_the_idempotency_key() -> None:
    missing: list[str] = []
    for relative_path, version_constant in _CALL_SITES:
        source = (_SRC / relative_path).read_text(encoding="utf-8")
        keys = _model_request_idempotency_keys(source)
        assert keys, f"{relative_path}: no ModelCompletionRequest idempotency_key found"
        missing.extend(
            f"{relative_path}: {key}" for key in keys if version_constant not in key
        )

    assert not missing, (
        "these model-call idempotency keys omit their prompt version, so a prompt "
        f"change would replay a stale cached completion: {missing}"
    )
