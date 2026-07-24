"""Addresses: ``name@hub``.

Two halves, and they do different jobs. The **name** identifies an actor and is opaque
(ADR 0003). The **hub** says which mailbox holds them, and is where a guarantee lives.

``local`` is a reserved alias for *this* mailbox, and it is a promise of **non-egress**:
an address ending ``@local`` can never be federated, whatever peering is arranged later.
That makes containment something an agent gets by choosing an address — visible by
inspection, with no configuration to get wrong. The same instinct as ``.local`` in mDNS,
and for the same reason.

Every hub therefore answers to two names: its own, and ``local``.

This mailbox does not federate yet. A message to another hub is **refused, loudly**,
rather than silently going nowhere — so an agent learns immediately, and so that
federation later turns a clear error into a delivery rather than changing what silence
meant.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_mailbox.exceptions import MalformedAddress, RemoteMailbox

#: The reserved alias for this mailbox, and the non-egress guarantee.
LOCAL = "local"

#: An address with no ``@`` part is local. Bare names are the common case, and making
#: them mean anything else would be a trap.
DEFAULT_HUB = LOCAL


@dataclass(frozen=True, slots=True)
class Address:
    """A parsed ``name@hub``."""

    name: str
    hub: str = DEFAULT_HUB

    def __str__(self) -> str:
        return f"{self.name}@{self.hub}"

    @property
    def guarantees_non_egress(self) -> bool:
        """Whether this address can *never* leave the mailbox it was written on.

        True only for the literal ``@local``. An address naming the hub by its own
        name is equivalent for delivery **today** but carries no such promise, because
        that name is meaningful to other hubs and this one is not.
        """
        return self.hub == LOCAL

    def is_local_to(self, hub_name: str) -> bool:
        """Whether this address is held by the hub called ``hub_name``."""
        return self.hub in (LOCAL, hub_name)


def parse(text: str, *, default_hub: str = DEFAULT_HUB) -> Address:
    """Parse ``name@hub``, or a bare ``name`` meaning this mailbox."""
    raw = text.strip()
    if not raw:
        raise MalformedAddress("an address cannot be empty")
    if raw.count("@") > 1:
        raise MalformedAddress(
            f"{text!r} has more than one '@' — addresses are name@hub"
        )
    name, _, hub = raw.partition("@")
    name, hub = name.strip(), hub.strip()
    if not name:
        raise MalformedAddress(f"{text!r} has no name before the '@'")
    if "@" in raw and not hub:
        raise MalformedAddress(f"{text!r} has no hub after the '@'")
    return Address(name=name.lower(), hub=(hub or default_hub).lower())


def local_name(text: str, hub_name: str = LOCAL) -> str:
    """The local actor name an address refers to, refusing anything we cannot reach.

    This is the boundary: above it the world is addresses, below it the messaging rules
    deal only in names. Keeping the split here is what lets the rules stay hub-agnostic
    — and lets federation later widen this one function rather than the whole engine.
    """
    address = parse(text)
    if not address.is_local_to(hub_name):
        raise RemoteMailbox(
            f"{address} is on another mailbox, and this one does not federate yet — "
            f"reachable addresses end in @{LOCAL} or @{hub_name}"
        )
    return address.name
