"""Mailbox errors as HTTP responses.

Mapped by **code**, not by class. M1 gave every error a stable machine-readable code
precisely so this layer could switch on it; a new error type gets a status by adding a
row here rather than by touching a handler.

Two choices worth knowing:

* ``no_such_message`` is **404 for both** "there is no such message" and "it exists but
  is not yours". Distinguishing them is the probe the visibility rules refuse to answer.
* Every body carries the code *and* a sentence saying what to do. The reader is usually
  an agent that cannot ask a follow-up question, so a bare status is not enough.
"""

from __future__ import annotations

from typing import Any

from litestar import Request, Response

from agent_mailbox.exceptions import MailboxError

#: code -> HTTP status. Anything unmapped is a bug in *our* code, not the caller's,
#: so it becomes a 500 rather than being guessed at.
STATUS_BY_CODE: dict[str, int] = {
    # the caller wrote something malformed
    "malformed_address": 400,
    "name_unavailable": 409,
    # well-formed, but names something that cannot be reached
    "unknown_recipient": 422,
    "remote_mailbox": 422,
    # the caller has not joined
    "unknown_actor": 404,
    # absent, or not yours — deliberately the same answer
    "no_such_message": 404,
    # a house rule said no
    "policy_refusal": 403,
    # our fault, not theirs
    "store_not_open": 503,
}


def problem(exc: MailboxError) -> dict[str, Any]:
    """The JSON body: what went wrong, machine-readably and in words."""
    body: dict[str, Any] = {"code": exc.code, "detail": str(exc)}
    policy = getattr(exc, "policy", None)
    if policy is not None:
        body["policy"] = policy
    return body


def mailbox_error_handler(request: Request, exc: MailboxError) -> Response:
    """Turn any MailboxError into an honest response.

    Never a traceback: an internal path leaking into a client is a disclosure, and an
    agent cannot do anything with one anyway.
    """
    status = STATUS_BY_CODE.get(exc.code, 500)
    return Response(content=problem(exc), status_code=status)
