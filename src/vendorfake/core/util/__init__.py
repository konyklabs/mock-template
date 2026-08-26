"""Small pure helpers shared by every core subsystem.

FOR: the handful of primitives that more than one subsystem needs and that
must behave identically wherever they are used — canonical JSON, the wire JSON
encoder, the hashes built on them.

INVARIANT: nothing here holds state, reads a clock, or knows what a request
is. A helper that needs any of those belongs to the subsystem that owns it,
because a shared helper with hidden state is how two subsystems silently start
disagreeing.
"""
