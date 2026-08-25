# Protection probe

Temporary file. Verifies that the branch ruleset on `main` permits a merge
once the review gate has approved — specifically, whether the gate's approval
satisfies the ruleset's required-review count when the merge is performed by
an automated session rather than a human.

Deleted immediately after the probe.
