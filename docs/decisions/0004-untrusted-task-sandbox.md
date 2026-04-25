# 0004 — Untrusted Task Sandbox

**Status:** Accepted
**Date:** 2026-04-24

## Context

The Implement and Verify phases of PCIV invoke ``python -m pytest`` inside a
git worktree whose contents may be partially or entirely produced by an LLM.
``pytest`` collects ``conftest.py``, autoloads installed plugins, and honors
``pytest_plugins`` declarations at collection time — meaning a model that can
write a file inside the worktree can execute arbitrary Python on the host
before any test code runs.

Prior to this ADR, both call sites invoked pytest as a subprocess on the host
with the workflow user's privileges. The flag allowlist in
``ImplementAgent._tool_run_pytest`` blocks malicious *args*, but is irrelevant
to ``conftest.py``-based payloads.

## Decision

Introduce a single chokepoint, ``pciv.sandbox.run_pytest``, with two modes
controlled by ``PlanConfig.runtime.task_trust``:

| Mode | Default? | Execution | Plugin autoload | conftest.py risk |
|---|---|---|---|---|
| ``trusted`` | no | host subprocess | disabled (``PYTEST_DISABLE_PLUGIN_AUTOLOAD=1``) | host-level |
| ``untrusted`` | **yes** | docker/podman container | disabled | container-level |

For ``untrusted`` we shell out to the first runtime found on PATH
(``docker`` then ``podman``) with:

* ``--rm --read-only --network=none --cap-drop=ALL --user 1001:1001``
* ``--tmpfs /tmp:rw,size=64m`` and a writable cache tmpfs
* a read-only bind mount of the worktree at ``/work``
* the same ``python:3.12-slim`` image the runtime stage uses

If neither runtime is available we **fail closed** with an actionable
error rather than silently degrading to host execution. Operators that
want host execution must opt in explicitly via
``runtime.task_trust: trusted`` and accept the threat model.

## Consequences

* Default install on a workstation without Docker/Podman now fails fast at
  the first verify step. ``pciv doctor`` surfaces the available runtime so
  the failure is diagnosable. Documented in ``docs/configuration.md``.
* CI matrix must include at least one runner with Docker (Ubuntu currently
  qualifies) so the sandbox path is exercised end-to-end. Trusted-mode tests
  cover the rest.
* The container adds ~1 s of cold-start latency per pytest invocation. Each
  iteration runs pytest once per subtask, so an iteration with N subtasks
  pays N × (~1 s) extra. Acceptable for a multi-agent loop where the
  dominant cost is the LLM round-trip.

## Out of scope

* Network-policy-based sandboxing (gVisor, Kata) — overkill for the current
  threat model.
* Sandboxing of the implement agent's ``write_file`` tool — already
  path-confined; the only new risk surface is *executing* what was written.
