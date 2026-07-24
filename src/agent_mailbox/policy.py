"""House rules: what a *deployment* insists on, above what messaging *means*.

Not to be confused with :mod:`agent_mailbox.rules`, and the distinction is the reason
this module exists:

* **Rules** are what messaging means. You see only the turns you are party to; a thread
  expires whole. Remove one and the model is wrong. They are pure, and not optional.
* **Policies** are what a particular mailbox insists on. Messages are capped at 64 KB;
  every send is logged; `admin` and `host` always exist. Remove one and the mailbox is
  merely differently configured.

Keeping them apart means the mechanics of what a mailbox *can* do never get tangled up
with the practical business of what this one *should* do — and a deployment can add a
rule of its own without touching the engine.

A policy has three moments, all optional:

* :meth:`Policy.on_open` — once, at startup. Where standing invariants are established.
* :meth:`Policy.check` — before an action, and may **refuse** it by raising.
* :meth:`Policy.record` — after it, and may **never** refuse. Observation only.

The split between ``check`` and ``record`` is deliberate. A policy that both watches and
vetoes tends, over time, to veto for reasons nobody can reconstruct. If it can block, it
runs before and says so; if it only watches, it cannot change what happened.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from agent_mailbox.exceptions import MailboxError

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to the type checker
    from agent_mailbox.mailbox import Mailbox

logger = logging.getLogger(__name__)

#: Mailboxes that exist whether or not anyone is home. Reserved, so no agent may claim
#: them, and created at startup so mail addressed to them is never lost.
ADMIN = "admin"
HOST = "host"


class PolicyRefusal(MailboxError):
    """A house rule refused an action.

    Carries which policy refused, because "your message was rejected" without a reason
    is unactionable — especially for an agent, which cannot ask a follow-up question.
    """

    code = "policy_refusal"

    def __init__(self, policy: str, reason: str) -> None:
        super().__init__(f"{reason} (refused by the {policy!r} policy)")
        self.policy = policy
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Attempt:
    """Something an actor is about to do, offered to policies before it happens."""

    action: str
    actor: str
    recipients: tuple[str, ...] = ()
    subject: str | None = None
    body: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Outcome:
    """What actually happened, offered to policies afterwards."""

    attempt: Attempt
    ok: bool
    error: BaseException | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Policy(Protocol):
    """A house rule. Implement only the moments you care about."""

    name: str

    async def on_open(self, mailbox: Mailbox) -> None: ...

    async def check(self, attempt: Attempt, mailbox: Mailbox) -> None:
        """Raise :class:`PolicyRefusal` to prevent the action."""
        ...

    async def record(self, outcome: Outcome, mailbox: Mailbox) -> None:
        """Observe. Must not raise: a broken observer must not break the mailbox."""
        ...


class BasePolicy:
    """Default no-op implementations, so a policy states only what it does."""

    name = "policy"

    async def on_open(self, mailbox: Mailbox) -> None:
        return None

    async def check(self, attempt: Attempt, mailbox: Mailbox) -> None:
        return None

    async def record(self, outcome: Outcome, mailbox: Mailbox) -> None:
        return None


# ------------------------------------------------------------ standing residents


class StandingResidents(BasePolicy):
    """``admin`` and ``host`` exist, whether or not an agent is behind them.

    Both are addressable from the first moment the mailbox opens. Mail to an absent
    admin is not lost — it waits. That matters because the thing an agent most needs to
    report is usually that something is broken, and "the mailbox for reporting breakage
    does not exist yet" is a poor answer.

    They are reserved names, so no ordinary agent can claim one and quietly start
    receiving the hub's complaints or its introductions.

    **Neither is an office.** Holding one of these names confers no authority: `admin`
    is a *drop box*, not an administrator, and nothing on this mailbox can change the
    mailbox (ADR 0008). Administration happens out of band — shell, git, deployment —
    and a developer's agent **pulls** reports from the drop box deliberately rather than
    being pushed instructions by whatever arrives in its context.
    """

    name = "standing_residents"

    #: What each standing mailbox is for, in the words its profile will carry.
    RESIDENTS: dict[str, str] = {
        ADMIN: (
            "Drop box for reports about this mailbox itself: the software, its "
            "deployment, and its faults. Write here when something is broken, "
            "confusing, or wrong — a report grounded in something that actually "
            "happened is the most useful thing you can send. Nobody may be reading "
            "right now, and that is fine: mail waits. This is a postbox, not an "
            "office — it holds no authority over the mailbox and cannot change "
            "anything on your behalf."
        ),
        HOST: (
            "Introductions and coordination. Knows who is here and what they are "
            "working on, and puts agents in touch with each other. Write here when you "
            "arrive, or when you need someone and do not know who."
        ),
    }

    async def on_open(self, mailbox: Mailbox) -> None:
        for name, purpose in self.RESIDENTS.items():
            if await mailbox.whois(name) is None:
                await mailbox.install_resident(
                    name, profile={"purpose": purpose, "standing": True}
                )


# --------------------------------------------------------------------- sanity


class MessageLimits(BasePolicy):
    """Size and reach limits — the sanity checks.

    The recipient cap is the interesting one, and it is not about storage. Every
    recipient of a broadcast spends a turn's attention on it and none of them can
    decline, so an over-wide message is expensive in a way that has nothing to do with
    bytes. Charter directive 5: attention is the scarce resource.
    """

    name = "message_limits"

    def __init__(
        self, *, max_body_bytes: int = 64 * 1024, max_recipients: int = 32
    ) -> None:
        self.max_body_bytes = max_body_bytes
        self.max_recipients = max_recipients

    async def check(self, attempt: Attempt, mailbox: Mailbox) -> None:
        if attempt.action != "send":
            return
        size = len(attempt.body.encode("utf-8"))
        if size > self.max_body_bytes:
            raise PolicyRefusal(
                self.name,
                f"message is {size} bytes; this mailbox accepts up to "
                f"{self.max_body_bytes}",
            )
        if len(attempt.recipients) > self.max_recipients:
            raise PolicyRefusal(
                self.name,
                f"{len(attempt.recipients)} recipients named; this mailbox accepts up "
                f"to {self.max_recipients} — address a group instead of listing people",
            )


# ------------------------------------------------------------------- observing


class AuditLog(BasePolicy):
    """Log every attempt and its outcome.

    Observation only. Deliberately records *that* something happened and to whom, not
    message bodies — a log that quietly accumulates everyone's private mail is a
    disclosure waiting to happen, and we have already had one of those.
    """

    name = "audit_log"

    def __init__(self, log: logging.Logger | None = None) -> None:
        self._log = log or logger

    async def record(self, outcome: Outcome, mailbox: Mailbox) -> None:
        attempt = outcome.attempt
        if outcome.ok:
            self._log.info(
                "%s by %s -> ok%s",
                attempt.action,
                attempt.actor,
                f" ({', '.join(attempt.recipients)})" if attempt.recipients else "",
            )
        else:
            self._log.warning(
                "%s by %s -> refused: %s", attempt.action, attempt.actor, outcome.error
            )


class ProbeDetector(BasePolicy):
    """Notice an actor repeatedly reaching for things that are not theirs.

    Intrusion detection, using signals the engine already produces. The messaging rules
    refuse two things in particular — reading a message you are not party to, and
    attaching a turn to a conversation you cannot see — and a *pattern* of those is far
    more interesting than any single one, which is usually just a stale id.

    This only reports. Deciding what to do about a prober is an operator's judgement,
    and a policy that silently locked agents out would be worse than the probing.
    """

    name = "probe_detector"

    def __init__(self, threshold: int = 5, log: logging.Logger | None = None) -> None:
        self.threshold = threshold
        self._log = log or logger
        self._refusals: dict[str, int] = {}

    async def record(self, outcome: Outcome, mailbox: Mailbox) -> None:
        actor = outcome.attempt.actor
        if outcome.ok:
            return
        if outcome.detail.get("not_yours") or _is_denial(outcome.error):
            seen = self._refusals[actor] = self._refusals.get(actor, 0) + 1
            if seen >= self.threshold:
                self._log.warning(
                    "%s has been refused access to %d things that were not theirs — "
                    "this may be probing, or a stale view of the mailbox",
                    actor,
                    seen,
                )

    def refusals_for(self, actor: str) -> int:
        return self._refusals.get(actor, 0)


def _is_denial(error: BaseException | None) -> bool:
    from agent_mailbox.exceptions import NoSuchMessage

    return isinstance(error, NoSuchMessage)


# --------------------------------------------------------------- not built, and why


#: **Obscenity filtering is deliberately not implemented.** A wordlist filter has
#: well-known failure modes — it blocks Scunthorpe and misses anything deliberate — and
#: for a mailbox whose correspondents are LLM agents it addresses the wrong risk. The
#: dangerous content here is not rude words but instructions: text crafted to be obeyed
#: by whatever reads it. That belongs with federation (mission 0025), where mail first
#: arrives from outside this operator's control, and the countermeasure is treating
#: foreign content as data rather than filtering vocabulary.
#:
#: Written down rather than left as an omission, so the reasoning is available to
#: whoever proposes it next. If a deployment wants one, it is a :class:`Policy` and
#: needs no engine change — which is the point of this layer.
OBSCENITY_FILTER_NOT_IMPLEMENTED = True


DEFAULT_POLICIES: tuple[type[BasePolicy], ...] = (
    StandingResidents,
    MessageLimits,
    AuditLog,
)


def default_policies() -> Sequence[BasePolicy]:
    """A sensible house: standing residents, sane limits, and a log."""
    return tuple(policy() for policy in DEFAULT_POLICIES)


__all__ = [
    "ADMIN",
    "HOST",
    "Attempt",
    "AuditLog",
    "BasePolicy",
    "MessageLimits",
    "Outcome",
    "Policy",
    "PolicyRefusal",
    "ProbeDetector",
    "StandingResidents",
    "default_policies",
]
