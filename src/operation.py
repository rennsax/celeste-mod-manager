from dataclasses import dataclass, replace
from enum import Enum


class IssueSeverity(Enum):
    WARNING = "warning"
    ERROR = "error"


class IssueKind(Enum):
    EMPTY_REQUIREMENTS = "empty_requirements"
    DOWNLOAD_FAILED = "download_failed"
    DATABASE_UNAVAILABLE = "database_unavailable"
    NOT_FOUND_IN_DB = "not_found_in_db"
    CHECKSUM_FAILED = "checksum_failed"
    ARCHIVE_INVALID = "archive_invalid"
    LOCAL_MOD_INVALID = "local_mod_invalid"
    DUPLICATE_LOCAL_MOD = "duplicate_local_mod"
    FILESYSTEM_ERROR = "filesystem_error"
    VERSION_MISMATCH = "version_mismatch"
    DATABASE_VERSION_MISMATCH = "database_version_mismatch"
    UNEXPECTED = "unexpected"


@dataclass(frozen=True)
class OperationIssue:
    severity: IssueSeverity
    kind: IssueKind
    operation: str
    subject: str
    detail: str
    dependency_chain: tuple[str, ...] = ()
    attempts: int | None = None
    retryable: bool = False
    hint: str | None = None

    def sort_key(self) -> tuple[str, str, str, str, tuple[str, ...], str]:
        return (
            self.subject.casefold(),
            self.severity.value,
            self.kind.value,
            self.operation.casefold(),
            tuple(part.casefold() for part in self.dependency_chain),
            self.detail.casefold(),
        )

    def with_dependency_chain(
        self, dependency_chain: tuple[str, ...]
    ) -> "OperationIssue":
        return replace(self, dependency_chain=dependency_chain)


def has_errors(issues: list[OperationIssue]) -> bool:
    return any(issue.severity == IssueSeverity.ERROR for issue in issues)
