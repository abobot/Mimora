"""Pure decision logic for the auto-pilot practice loop.

The auto-pilot loop drives the main practice loop: when enabled, a take whose
raw 0-100 engine score clears the threshold advances to the next phrase, which
plays the reference and then records after the configured delay, and so on.
This module holds the pure decision so it can be tested without the GUI stack
(the loop's Tk scheduling lives in ``mimora/app.py``).
"""


def should_advance(enabled: bool, raw_score: float, threshold: float,
                   gate_enabled: bool, passed: bool) -> bool:
    """Decide whether the auto-pilot loop advances past this take (pure).

    The loop advances only when the master toggle is on, the take's raw 0-100
    engine score is STRICTLY GREATER than the threshold (the user's "x分"),
    and the take is allowed to advance anyway: either the pass gate is off, or
    the take actually passed it. A take at or below the threshold - or one the
    gate refuses - does NOT advance: the loop stays on the same phrase and
    re-records it after the record delay (mirrors the pass gate's "repeat
    until it passes" semantics).

    ``raw_score`` is the engine's raw 0-100 score, NOT the 0-5 grade shown for
    the phoneme engine: the threshold must mean the same thing across every
    engine, and the pass bucket is itself derived from the raw score.
    """
    if not enabled:
        return False
    if raw_score <= 0.0:
        return False  # an empty/failed take is discarded, never looped
    if raw_score <= threshold:
        return False
    if gate_enabled and not passed:
        return False
    return True
