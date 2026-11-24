"""Safe subprocess execution.

Contract (TECHNICAL_DECISIONS ADR-004 / security tests §5):
- Command lists only — never a shell string; `shell=True` is structurally impossible here.
- Hard timeout with process kill; partial output preserved in the raised error.
- Output size caps so a chatty child process cannot exhaust memory.
- UTF-8 decoding with replacement so hostile/odd bytes never crash the CLI.
"""

from __future__ import annotations

import dataclasses
import subprocess
import time
from collections.abc import Sequence

from creator_core.cli.errors import CreatorError

DEFAULT_TIMEOUT_S = 60.0
DEFAULT_MAX_OUTPUT = 4_000_000  # bytes per stream
_TRUNCATED_MARKER = b"\n...[output truncated by creator_core]\n"


class SubprocessTimeoutError(CreatorError):
    exit_code = 1
    code = "subprocess_timeout"

    def __init__(
        self, cmd: Sequence[str], timeout: float, partial_stdout: bytes, partial_stderr: bytes
    ):
        self.partial_stdout = partial_stdout
        self.partial_stderr = partial_stderr
        super().__init__(
            f"process timed out after {timeout:.1f}s: {cmd[0] if cmd else '<empty>'}",
            hint="raise the limit in config (limits.subprocess_timeout_s) or check the input",
        )


@dataclasses.dataclass
class RunResult:
    args: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    stdout_truncated: bool
    stderr_truncated: bool


def _cap(data: bytes, limit: int) -> tuple[bytes, bool]:
    if len(data) <= limit:
        return data, False
    return data[:limit] + _TRUNCATED_MARKER, True


def run(
    cmd: Sequence[str],
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    cwd: str | None = None,
    env_overrides: dict[str, str] | None = None,
    max_output: int = DEFAULT_MAX_OUTPUT,
) -> RunResult:
    """Run `cmd` (list of str) and capture output. Raises SubprocessTimeoutError on timeout."""
    if isinstance(cmd, str):
        raise TypeError("cmd must be a sequence of strings; shell strings are not accepted")
    if not cmd:
        raise ValueError("cmd must not be empty")

    import os

    env = None
    if env_overrides:
        env = os.environ.copy()
        env.update(env_overrides)

    start = time.monotonic()
    proc = subprocess.Popen(
        [str(c) for c in cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        shell=False,
    )
    try:
        raw_out, raw_err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        partial_out, partial_err = proc.communicate()
        raise SubprocessTimeoutError(
            list(map(str, cmd)), timeout, partial_out or b"", partial_err or b""
        ) from None
    duration = time.monotonic() - start

    out, out_trunc = _cap(raw_out or b"", max_output)
    err, err_trunc = _cap(raw_err or b"", max_output)
    return RunResult(
        args=[str(c) for c in cmd],
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout=out.decode("utf-8", errors="replace"),
        stderr=err.decode("utf-8", errors="replace"),
        duration_s=duration,
        stdout_truncated=out_trunc,
        stderr_truncated=err_trunc,
    )
