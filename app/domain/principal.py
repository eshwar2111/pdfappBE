"""The unified actor abstraction.

Every request — whether it carries a registered user's access token or an
invited guest's scoped token — is resolved into exactly one ``Principal``
before it reaches a controller. Routers and services never branch on
"is this a user or a guest?"; they ask the principal what it is allowed to do.

This is what keeps guest support from leaking into every endpoint, and it is
what guarantees a guest can never act outside the single document their share
link grants them.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.domain.enums import Permission, PrincipalKind


@dataclass(frozen=True, slots=True)
class Principal:
    kind: PrincipalKind
    id: UUID
    display_name: str
    permissions: frozenset[Permission]

    #: For guests this is pinned to the one document their share link covers.
    #: For registered users it is ``None`` — their reach is decided per-resource
    #: by ownership checks, not by the token.
    document_scope: UUID | None = None

    #: Set only for guests; lets us record which share link was used.
    share_id: UUID | None = None

    @property
    def is_user(self) -> bool:
        return self.kind is PrincipalKind.USER

    @property
    def is_guest(self) -> bool:
        return self.kind is PrincipalKind.GUEST

    @property
    def user_id(self) -> UUID | None:
        return self.id if self.is_user else None

    @property
    def guest_session_id(self) -> UUID | None:
        return self.id if self.is_guest else None

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions

    def may_reach(self, document_id: UUID) -> bool:
        """Token-level scope check, performed before any ownership lookup."""
        return self.document_scope is None or self.document_scope == document_id
