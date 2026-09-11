# 09. Tool, Skill, Plugin, and Sub-Agent Integration

## Goal

Claude, Codex, or another coding agent should call one stable tool contract. The
caller does not need to understand Loki, sandboxes, databases, or rollout
controllers.

## Primary Public Tool

```json
{
  "name": "optimize_codebase",
  "input": {
    "codebase": "local path or Git URL",
    "feature": "business feature",
    "objective": "measurable objective",
    "criteria": [],
    "mode": "manual or automatic",
    "scope": {},
    "execution_policy": "suggest_only | plan_only | sandbox_patch | pr"
  }
}
```

The response never blocks indefinitely:

```json
{
  "case_id": "OPT-...",
  "thread_id": "...",
  "status": "running | interrupted | completed | failed",
  "current_node": "A2",
  "interrupt": null,
  "artifact_refs": {},
  "next_action": "poll | resume | inspect"
}
```

## Supporting Tools

| Tool | Purpose |
|---|---|
| `get_optimization_case` | Read current state and artifact references |
| `stream_optimization_events` | Observe node start, completion, and logs |
| `resume_optimization` | Submit approval or clarification for an interrupt ID |
| `cancel_optimization` | Cancel the graph and active worker jobs |
| `download_artifact` | Retrieve an artifact after authorization |
| `list_solutions` | Inspect findings, tradeoffs, and ranking |
| `select_solution` | Resume node 01 with a selection |
| `approve_phase` | Resume node 02 or 03 |
| `approve_rollout` | Resume node 08 |

## MCP Server

The MCP adapter maps tool calls to the LangGraph service:

```text
Claude or Codex
  -> MCP tool
  -> API authentication and RBAC
  -> LangGraph invoke or resume by thread_id
  -> checkpoint and artifact references
  -> structured response
```

The MCP process keeps no workflow state in memory and receives no secret in a
tool argument. Identity comes from the host authentication layer; the worker
obtains secrets from a secret manager.

## Skill Contract

`SKILL.md` should instruct the host agent to:

1. Gather the minimum feature, objective, and criteria.
2. Call `optimize_codebase` once.
3. Poll or stream the same case instead of starting another run.
4. When interrupted, show the exact artifact digest and choices to the user.
5. Resume with the same `case_id` and `thread_id`.
6. Never generate evidence files, mutate artifacts, or bypass a quality gate.
7. Never call the LLM provider directly when the control plane owns A3.

A skill is a UX and invocation contract, not a home for business logic.

## Coding-Agent Adapters

Node 03 exposes one interface:

```python
class CodingAgentPort(Protocol):
    def implement(self, job: ApprovedPhaseJob) -> PatchArtifact: ...
```

Candidate adapters:

- OpenHands SDK: default self-hostable backend [SRC-OPENHANDS].
- Claude Agent SDK: provider backend with built-in code tools [SRC-CLAUDE].
- Codex tool or adapter: workspace coding backend [SRC-CODEX].
- SWE-agent: benchmark or fallback backend [SRC-SWEAGENT].

Every adapter passes the same conformance tests for scope, timeout,
cancellation, transcript capture, patch format, secret isolation, and the
deterministic artifact envelope.

## One Command, One Thread

The CLI is a client of the durable workflow:

```powershell
optimizer-platform run --request request.json --follow
```

The command creates one `case_id` and `thread_id`, then follows that thread. If
approval is required, the CLI or UI resumes the checkpoint. It does not restart
A1 or A2 and does not ask the user to copy intermediate artifact paths.

In production, "one command" does not mean bypassing approval. It means one case
continues durably through interrupts until it reaches a terminal state.

