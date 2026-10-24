"""Finish session work before propagating cancellation to its lock owner."""

import asyncio


async def finish_before_cancel(awaitable):
    """Keep executor work alive, including during repeated cancellation.

    A cancelled executor future cannot stop its thread. The caller must keep
    holding the session lock until this awaitable has restored the parent.
    Cancellation still wins over a result or an exception once work finishes.
    """
    pending = asyncio.ensure_future(awaitable)
    cancellation = None
    while not pending.done():
        try:
            # wait() never forwards cancellation to its input future. Unlike
            # shield(), it also does not log the worker's original exception
            # on Python 3.14 after the caller was cancelled.
            await asyncio.wait((pending,))
        except asyncio.CancelledError as exc:
            if pending.cancelled():
                raise
            cancellation = exc
    if cancellation is not None:
        # Retrieve a worker exception so it is never logged as unhandled.
        if not pending.cancelled():
            pending.exception()
        raise cancellation
    return pending.result()
