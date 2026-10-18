class WorklogError(Exception):
    """A user-actionable failure; the CLI emits a structured error."""
    code = "worklog_error"


class Conflict(WorklogError):
    code = "conflict"


class Corruption(WorklogError):
    code = "corruption"


class NotFound(WorklogError):
    code = "not_found"


class Invalid(WorklogError):
    code = "invalid"


class Busy(Conflict):
    code = "busy"


class Capacity(Invalid):
    code = "capacity"


class MigrationRequired(WorklogError):
    code = "migration_required"
