"""What the mailbox raises, and why each case is its own class.

Two things shape this hierarchy.

**Failures that need different responses must be different types.** Sending to a name
that does not exist *here* is a mistake the sender can fix by correcting it. Sending to
another mailbox entirely is a thing this deployment cannot do *yet*, and will be able to
do later. Collapsing them would tell an agent "that didn't work" and leave it to guess.

**Every error carries a stable ``code``.** Prose is for the agent reading it and may be
reworded freely; the code is for the layer above, which maps it to an HTTP status or an
MCP error without pattern-matching on English.

The one deliberate *fusion* is :class:`NoSuchMessage`, which covers both "no such
message" and "not yours". Distinguishing those would let an outsider probe what is
stored, which is precisely what the visibility rules protect.
"""

from __future__ import annotations


class MailboxError(Exception):
    """Base for everything this package raises.

    ``code`` is the machine-readable half. Subclasses set it; callers switch on it.
    """

    code = "mailbox_error"


# -- identity ---------------------------------------------------------------


class NameUnavailable(MailboxError):
    """A requested name is taken, reserved, or malformed.

    Recoverable by choosing differently, or by asking for one to be issued.
    """

    code = "name_unavailable"


class UnknownActor(MailboxError):
    """The **caller** has not joined this mailbox.

    Distinct from :class:`UnknownRecipient`: this is about who is acting, not who is
    being written to, and the fix is to join rather than to correct an address.
    """

    code = "unknown_actor"


# -- addressing -------------------------------------------------------------


class AddressError(MailboxError):
    """Base for anything wrong with an address.

    Catch this to handle every addressing failure alike; catch a subclass when the
    difference matters, which it usually does.
    """

    code = "address_error"


class MalformedAddress(AddressError):
    """The address could not be parsed at all — empty, or not ``name@hub``.

    A syntax error. Nothing was looked up, because there was nothing to look up.
    """

    code = "malformed_address"


class UnknownRecipient(AddressError):
    """No such actor **on this mailbox**.

    The address is well-formed and local; nobody by that name has joined. Almost always
    a typo or a stale name, and always the sender's to fix.

    This is raised rather than delivered-to-nobody on purpose. A message that reports
    success and reaches no one is the worst outcome for an agent, which cannot notice
    the silence and will wait for a reply that is never coming. Groups are the exception
    — an empty group is legitimately empty — so only a specific name raises.
    """

    code = "unknown_recipient"


class RemoteMailbox(AddressError):
    """The address names a **different mailbox**, which this one cannot reach.

    Not a mistake by the sender: the address may be perfectly valid somewhere. This
    deployment does not federate, so there is nowhere to send it *yet*.

    Kept distinct from :class:`UnknownRecipient` because the remedies are opposite —
    one is "fix the name", the other is "this needs federation" — and because when
    federation arrives, this case becomes a delivery while that one still fails.
    """

    code = "remote_mailbox"


# -- storage ----------------------------------------------------------------


class StoreNotOpen(MailboxError):
    """A store was used before it was opened, or after it was closed.

    A misuse rather than a condition — but it gets a named class anyway, because a
    caller that catches this can open the store and retry, and one that catches
    ``RuntimeError`` catches everything else the interpreter raises too.
    """

    code = "store_not_open"


# -- messages ---------------------------------------------------------------


class NoSuchMessage(MailboxError):
    """No message with that id is available **to you**.

    Deliberately one error for two situations — it does not exist, and it exists but is
    not yours. Distinguishing them would let an outsider probe what is stored, which is
    the same reasoning that makes an unseen thread come back empty rather than
    forbidden.
    """

    code = "no_such_message"
