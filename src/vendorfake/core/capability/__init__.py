"""Capabilities: which parts of a vendor's surface and conduct are switched on.

FOR: turning "consumer subsets are configuration, never code" into a mechanism
-- a profile names a subset, the registry resolves it, and everything else asks
the registry rather than reading the profile.

INVARIANT: **a capability that is off is answered, not hidden.** Hiding a route
would make "disabled in this profile" indistinguishable from "this vendor has
no such endpoint", which is the ambiguity that wastes a consumer's afternoon.
Every disabled call gets ``capability_disabled``, naming the capability, the
blocker and the profile.

Its companion invariant lives in :mod:`vendorfake.core.capability.gates`:
silence is a failure. A capability the core itself gates on that the vendor
neither declares nor records as unsupported is an error at construction, not a
behaviour that is quietly off forever.
"""
