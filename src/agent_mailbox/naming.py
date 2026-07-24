"""Names: requested by the agent, adjudicated by the hub, or issued when absent.

A name is **opaque**. It carries no meaning the system routes on, which is the whole
point of ADR 0003 — our previous identifier was assembled from project, engine and role,
and every one of those facts eventually changed.

An agent may pick its own; the hub decides whether it gets it. What the hub
guarantees is **uniqueness**, which nothing enforced before — so two agents
sharing a name silently shared an inbox.

Issued names come from a checked-in pool (:mod:`agent_mailbox.name_pool`) — no
generator library at runtime, because it is, in the end, two lists of words.
"""

from __future__ import annotations

import random
import re
import unicodedata
from dataclasses import dataclass

from agent_mailbox.exceptions import NameUnavailable
from agent_mailbox.name_pool import FAMILY_NAMES, GIVEN_NAMES

#: Reserved: addressing keywords, not names anyone may hold. ``local`` matters
#: most — it is a guarantee of non-egress, so it must never be something an
#: agent can be called.
RESERVED_NAMES: frozenset[str] = frozenset({"local", "all", "any", "public", "me"})

_VALID = re.compile(r"^[a-z0-9](?:[a-z0-9_]{0,62}[a-z0-9])?$")


@dataclass(frozen=True, slots=True)
class Name:
    """A validated name.

    Frozen because a name is stable for the life of the actor. Changing facts must not
    change identity — that lesson cost six missions (ADR 0003).
    """

    value: str

    def __str__(self) -> str:
        return self.value


def normalize(raw: str) -> str:
    """Reduce a proposed name to its canonical form: ASCII, lowercase, underscored.

    Latin diacritics are folded, so ``Zoë Müller`` and ``zoe_muller`` cannot become two
    different actors. Anything not reducible to ASCII — Cyrillic, CJK, Arabic — reduces
    to something empty or partial and is refused by :func:`validate` with a clear
    message, rather than being silently transliterated into a name the agent did not
    choose.

    This is the one deliberate Western bias in the design, and it is the owner's call:
    names are strictly ASCII, lowercase, underscore-separated. An agent whose name is
    written in another script picks its own romanisation — which is what people do
    anyway, and a better outcome than a machine guessing a reading and being wrong.
    """
    decomposed = unicodedata.normalize("NFKD", raw.strip())
    folded = "".join(c for c in decomposed if not unicodedata.combining(c))
    underscored = re.sub(r"[\s\-.']+", "_", folded.lower())
    collapsed = re.sub(r"_{2,}", "_", underscored)
    return re.sub(r"[^a-z0-9_]", "", collapsed).strip("_")


def validate(raw: str) -> Name:
    """Validate a proposed name, or explain precisely why it cannot be used.

    The message matters: an agent reads it and has to act on it unaided.
    """
    candidate = normalize(raw)
    if not candidate:
        raise NameUnavailable(
            f"{raw!r} has no usable characters — names are ASCII letters, digits and "
            "underscores, so pick a romanised form (for example 'yitzhak_levin')"
        )
    if candidate in RESERVED_NAMES:
        raise NameUnavailable(
            f"{candidate!r} is reserved for addressing and cannot be a name; "
            f"reserved: {', '.join(sorted(RESERVED_NAMES))}"
        )
    if not _VALID.match(candidate):
        raise NameUnavailable(
            f"{candidate!r} is not a usable name — 1 to 64 characters, starting and "
            "ending with a letter or digit"
        )
    return Name(candidate)


def generate(seed: int | None = None) -> str:
    """Propose a name from the checked-in pool.

    Given and family names are drawn **independently**, so most results cross
    traditions — ``rosemary_nasrin``, ``trevor_mahmood``. That is the intent: the
    workforce is explicitly and absurdly multicultural, and pairing within a tradition
    would give a tidier, duller, less representative result.

    Returns a *candidate*. Uniqueness is the directory's job — a generator cannot know
    what is already taken.
    """
    rng = random.Random(seed)
    return f"{rng.choice(GIVEN_NAMES)}_{rng.choice(FAMILY_NAMES)}"
