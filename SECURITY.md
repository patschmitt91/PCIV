# Security Policy

## Supported versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | Yes                |
| < 0.1   | No                 |

Only the latest minor release line receives security fixes.

## Reporting a vulnerability

Please do **not** open a public GitHub issue for security reports.

Use GitHub's private vulnerability reporting:
<https://github.com/patschmitt91/PCIV/security/advisories/new>

Or email the maintainer at
<patschmitt91@users.noreply.github.com> with a description and a
minimal reproduction.

You should receive an acknowledgement within 5 business days.

## Disclosure window

We follow a 90-day coordinated disclosure window. Confirmed
vulnerabilities will be patched and publicly disclosed no later than
90 days after the initial report, or sooner once a fix is released,
whichever comes first. Extensions may be negotiated with the reporter
for complex issues.

## Task content trust boundary

The Implement and Verify phases execute ``python -m pytest`` inside a
git worktree whose contents may be (in part) authored by an LLM. A
malicious ``conftest.py`` would otherwise execute with full host
privileges as soon as pytest collects the worktree.

PCIV mitigates this through ``PlanConfig.runtime.task_trust``, which
gates a single chokepoint in ``pciv.sandbox.run_pytest``:

* **``untrusted`` (default)** — pytest runs inside a short-lived Docker
  or Podman container with ``--read-only --network=none --cap-drop=ALL
  --user 1001:1001`` and a read-only bind mount of the worktree. The
  ``PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`` env var is always set so even
  installed plugins do not load. If neither container runtime is on
  PATH the call **fails closed** rather than degrading to host
  execution.
* **``trusted``** — pytest runs as a host subprocess with plugin
  autoload still disabled. Use this only when the task content is
  fully internal and you accept that a hand-crafted ``conftest.py`` in
  the worktree will execute as the workflow user.

This boundary does **not** protect against:

* Outbound network data exfiltration if you pass ``trusted`` and the
  host has unrestricted egress.
* Anything the model writes via ``_tool_write_file``; only the
  *execution* of that content is sandboxed (the file system is still
  modified by the implement loop).
* Compromise of the container runtime itself (gVisor / Kata are out of
  scope; see ``docs/decisions/0004-untrusted-task-sandbox.md``).
