"""Recommendation engine orchestrator.

Runs all registered scorers, sums and normalizes scores to 0.0-1.0,
and stores score_breakdown JSONB for transparency.
"""
