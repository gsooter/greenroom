"""Lightweight health checks that watch external sources for state changes.

Distinct from :mod:`backend.scraper.validator`, which validates the
results of a *completed* scrape run: watchdogs sit on venues we have no
scraper for yet and ping us when a source becomes tractable.

Today this package owns a single watchdog — DC9's DICE widget. Expect
more as we onboard venues whose data pipeline isn't stable at launch.
"""
