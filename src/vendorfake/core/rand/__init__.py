"""The seeded random stream.

FOR: the few places that genuinely need randomness — a chaos rule that asks
for a probability, and vendor id generation.

INVARIANT: chaos *triggering* never consults this. Triggering is counter-based
so that "the third create fails" is a fact rather than a flake; the RNG is
drawn from only after every deterministic condition has already passed.
"""
