"""Structured error model and exit codes for the `creator` CLI.

Exit codes (contract v1.0, ARCHITECTURE.md §8):
  0 ok · 1 unexpected · 2 validation/unsafe input · 3 missing dependency ·
  4 auth required · 5 approval required · 6 I/O · 7 provider/API error
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_VALIDATION = 2
EXIT_MISSING_DEPENDENCY = 3
EXIT_AUTH_REQUIRED = 4
EXIT_APPROVAL_REQUIRED = 5
EXIT_IO = 6
EXIT_PROVIDER = 7


class CreatorError(Exception):
    """Base class for all expected, user-facing errors."""

    exit_code = EXIT_UNEXPECTED
    code = "internal_error"

    def __init__(self, message: str, *, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def payload(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "hint": self.hint}}

    def __str__(self) -> str:
        if self.hint:
            return f"{self.message} ({self.hint})"
        return self.message


class ValidationError(CreatorError):
    exit_code = EXIT_VALIDATION
    code = "validation_error"


class UnsafePathError(ValidationError):
    code = "unsafe_path"


class PathNotFoundError(CreatorError):
    exit_code = EXIT_IO
    code = "path_not_found"


class MissingDependencyError(CreatorError):
    exit_code = EXIT_MISSING_DEPENDENCY
    code = "missing_dependency"


class AuthRequiredError(CreatorError):
    exit_code = EXIT_AUTH_REQUIRED
    code = "auth_required"


class ApprovalRequiredError(CreatorError):
    exit_code = EXIT_APPROVAL_REQUIRED
    code = "approval_required"


class WorkspaceIOError(CreatorError):
    exit_code = EXIT_IO
    code = "workspace_io"


class ProviderError(CreatorError):
    exit_code = EXIT_PROVIDER
    code = "provider_error"


class ProviderUnavailableError(ProviderError):
    code = "provider_unavailable"
