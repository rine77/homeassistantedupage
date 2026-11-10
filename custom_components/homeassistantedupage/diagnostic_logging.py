"""Exception stacks without payloads, URLs, source literals or local variables."""

from pathlib import PurePath

_SAFE_REASONS = {
    "Ambiguous parent account identity",
    "Parent switch redirected to login",
}


def safe_exception_stack(error):
    """Keep the original exception types/frames; never format arbitrary args."""
    sections = []
    seen = set()
    while error is not None and id(error) not in seen and len(seen) < 8:
        seen.add(id(error))
        lines = ["Traceback (sanitized; source lines and local values omitted):"]
        trace = error.__traceback__
        while trace is not None:
            code = trace.tb_frame.f_code
            lines.append(f'  File "{PurePath(code.co_filename).name}", line {trace.tb_lineno}, in {code.co_name}')
            trace = trace.tb_next
        reason = error.args[0] if error.args and isinstance(error.args[0], str) else None
        suffix = f": {reason}" if reason in _SAFE_REASONS else ""
        lines.append(type(error).__name__ + suffix)
        sections.append("\n".join(lines))
        next_error = error.__cause__
        if next_error is None and not error.__suppress_context__:
            next_error = error.__context__
        error = next_error
    return "\nCaused by (sanitized):\n".join(sections)


def log_exception(logger, operation, error, *, entry_id=None, student_id=None):
    """Log the original stack without handing an unsafe exc_info to handlers."""
    logger.warning(
        "EduPage %s failed entry_id=%s student_id=%s exception=%s\n%s",
        operation, entry_id if isinstance(entry_id, (str, int)) else "unknown",
        student_id if isinstance(student_id, (str, int)) else "unknown",
        type(error).__name__, safe_exception_stack(error),
    )
