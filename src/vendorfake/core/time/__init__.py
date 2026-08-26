"""The clock and the timer scheduler.

FOR: making behaviour that is measured in vendor-scale time — a thirty-day
token life, a twenty-four-hour webhook retry schedule — observable in a
millisecond.

INVARIANT: no core subsystem reads the wall clock directly. Every timestamp
and every scheduled callback goes through ``Clock``, which is the only reason
``virtual`` mode can make a whole retry cascade collapse into one call.
"""
