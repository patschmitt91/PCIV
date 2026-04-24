"""Parse README code blocks and sanity-check shell commands (no execution)."""

from __future__ import annotations

import re
import shlex
import shutil
from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"

_KNOWN_EXECUTABLES = {
    "uv",
    "pip",
    "python",
    "python3",
    "pytest",
    "budgeteer",
    "pciv",
    "git",
    "az",
    "docker",
    "make",
    "npm",
    "pnpm",
    "node",
    "ruff",
    "mypy",
    "pre-commit",
    "sqlite3",
}

_CODE_FENCE = re.compile(r"^```([\w-]*)\s*$")


def _iter_code_blocks(text: str) -> list[tuple[str, list[str]]]:
    lines = text.splitlines()
    blocks: list[tuple[str, list[str]]] = []
    i = 0
    while i < len(lines):
        m = _CODE_FENCE.match(lines[i])
        if not m:
            i += 1
            continue
        lang = m.group(1).lower()
        body: list[str] = []
        i += 1
        while i < len(lines) and not _CODE_FENCE.match(lines[i]):
            body.append(lines[i])
            i += 1
        i += 1
        blocks.append((lang, body))
    return blocks


def _looks_like_shell(lang: str) -> bool:
    return lang in {"", "bash", "sh", "shell", "console", "zsh"}


def _command_lines(block: list[str]) -> list[str]:
    cmds: list[str] = []
    buf: list[str] = []
    for raw in block:
        line = raw.rstrip()
        stripped = line.lstrip()
        if not stripped:
            if buf:
                cmds.append(" ".join(buf))
                buf = []
            continue
        if stripped.startswith("$ "):
            if buf:
                cmds.append(" ".join(buf))
                buf = []
            stripped = stripped[2:].rstrip()
        if stripped.endswith("\\"):
            buf.append(stripped[:-1].rstrip())
            continue
        buf.append(stripped)
        cmds.append(" ".join(buf))
        buf = []
    if buf:
        cmds.append(" ".join(buf))
    return cmds


def _first_token(cmd: str) -> str | None:
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return None
    return parts[0] if parts else None


def test_readme_commands_are_syntactically_valid() -> None:
    text = README.read_text(encoding="utf-8")
    blocks = _iter_code_blocks(text)
    assert blocks, "README has no code blocks"

    checked = 0
    for lang, body in blocks:
        if not _looks_like_shell(lang):
            continue
        for cmd in _command_lines(body):
            exe = _first_token(cmd)
            if exe is None:
                continue
            if exe not in _KNOWN_EXECUTABLES:
                continue
            shlex.split(cmd)
            if exe in {"budgeteer", "pciv"}:
                checked += 1
                continue
            assert shutil.which(exe), f"README references {exe!r} which is not on PATH"
            checked += 1

    assert checked > 0, "README has no recognized shell command lines"
