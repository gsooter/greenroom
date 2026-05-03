"""Score overlays applied after the per-event scorer combine step.

The recommendation engine combines per-scorer outputs into a single
``base_score`` per (user, event) pair, then multiplies that score by
each overlay in this package. The overlays encode "is this show
worth attending right now?" — locality, timing, and availability —
without changing the relative ranking *within* any single scorer's
output.

Each overlay is a pure function of the event (and, for actionability,
the user's preferred-city/region). They are intentionally small and
side-effect free so the engine can apply them in O(1) per event.
"""
