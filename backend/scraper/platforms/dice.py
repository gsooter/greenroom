"""Dice.fm venue scraper using JSON-LD structured data extraction.

Dice.fm embeds full event data in the raw HTML of every venue page, so
this scraper does not need a headless browser. It fetches the page with
a standard browser User-Agent, parses the embedded structured data, and
yields one ``RawEvent`` per show.

JSON-LD is the primary source because it is explicitly designed for
machine consumption and Dice has a strong SEO incentive to keep it
accurate. ``__NEXT_DATA__`` is a fallback: if Dice ever removes the
JSON-LD, the Next.js bootstrap payload still carries the same events
under a vendor-specific shape, which keeps the scraper alive through
one more redesign cycle.

If neither source yields events the scraper raises
:class:`DiceScraperError` so the runner marks the run FAILED and the
validator alerts within one nightly cycle.

Investigation findings (captured against live pages on 2026-04-24,
User-Agent: Chrome/122 desktop, no JavaScript executed):

1. JSON-LD is present in the raw HTML — no client-side rendering is
   required. Each venue page carries four
   ``<script type="application/ld+json">`` blocks:

   - block #0: a ``Place`` node with a ``name``, ``address``, ``geo``
     (``latitude``/``longitude``), and an ``event`` array containing
     every upcoming event at the venue (30 events on DC9, Songbyrd,
     and BERHTA; 2 on Byrdland at the time of inspection).
   - block #1: a ``Brand`` node for DICE itself — not useful.
   - block #2, #3: ``WebSite`` nodes — not useful.

   Only block #0 matters for scraping.

2. Each event inside ``Place.event`` is a self-contained schema.org
   ``Event`` node with fields we care about:

   - ``@type`` = ``"Event"``
   - ``name`` — event title (e.g. "Nerd Nite")
   - ``url`` — absolute Dice event URL, acts as a stable external id
   - ``startDate`` — ISO 8601 with timezone offset, e.g.
     ``"2026-04-24T20:00:00-04:00"`` (already America/New_York-aware,
     does NOT need localization)
   - ``endDate`` — same format; optional
   - ``image`` — list of URLs (usually one)
   - ``description`` — free text, often includes doors time and age
     restrictions
   - ``location`` — nested ``Place`` with name, address, geo
   - ``offers`` — often an empty list on browse listings; occasionally
     populated with ``Offer`` objects carrying ``price``/``lowPrice``
     and ``url``
   - ``performer`` — typically absent on browse listings; __NEXT_DATA__
     carries the richer ``summary_lineup`` block when we need it
   - ``organizer`` — ``{"@type": "Organization", "name": "..."}``

3. ``__NEXT_DATA__`` is also present under
   ``<script id="__NEXT_DATA__">``. The event array lives at
   ``props.pageProps.profile.sections[*].events`` — the sections are
   usually "Upcoming" (index 0), but we traverse all of them to be
   safe against future section splits ("This week", "Next month").
   Each event has native Dice fields:

   - ``perm_name`` — Dice's stable event slug, forms the URL:
     ``https://dice.fm/event/{perm_name}``
   - ``name``, ``images.landscape``/``portrait``/``square``
   - ``dates.event_start_date``, ``dates.event_end_date``,
     ``dates.timezone`` (IANA), ``venues[0].doors_open_date``
   - ``price.amount`` (in *cents*, USD), ``price.currency``
   - ``summary_lineup.top_artists`` — list of ``{name, artist_id,
     is_headliner, image}`` objects; full lineup breakdown
   - ``tags_types`` — list of ``{name, value, title}`` genre-ish tags

4. JavaScript rendering is NOT required. The initial HTML response
   (status 200, ~566 KB on DC9) contains every field above.

Field mapping (primary, JSON-LD):

  RawEvent.title               ← event.name
  RawEvent.venue_external_id   ← constructor ``venue_external_id``
  RawEvent.starts_at           ← parse(event.startDate)  (tz-aware)
  RawEvent.ends_at             ← parse(event.endDate)    (optional)
  RawEvent.source_url          ← event.url
  RawEvent.ticket_url          ← event.url (Dice event URL is the
                                  ticket URL on Dice)
  RawEvent.image_url           ← event.image[0]
  RawEvent.description         ← event.description
  RawEvent.artists             ← performer names when present, else
                                  [event.name]
  RawEvent.min_price           ← offers[].price or offers[].lowPrice
  RawEvent.max_price           ← offers[].price or offers[].highPrice
  RawEvent.on_sale_at          ← offers[].validFrom
  RawEvent.raw_data            ← the full JSON-LD event node verbatim

Field mapping (fallback, __NEXT_DATA__):

  RawEvent.title               ← event.name
  RawEvent.venue_external_id   ← constructor ``venue_external_id``
  RawEvent.starts_at           ← parse(event.dates.event_start_date)
  RawEvent.ends_at             ← parse(event.dates.event_end_date)
  RawEvent.source_url          ← "https://dice.fm/event/" + perm_name
  RawEvent.ticket_url          ← same as source_url
  RawEvent.image_url           ← event.images.landscape (fall back to
                                  square, portrait)
  RawEvent.description         ← event.about.description (if present)
  RawEvent.artists             ← [a.name for a in summary_lineup
                                  .top_artists] or [event.name]
  RawEvent.min_price           ← event.price.amount / 100.0
  RawEvent.max_price           ← event.price.amount / 100.0
  RawEvent.raw_data            ← the full NEXT_DATA event node

DC venues using this scraper:

  dc9       https://dice.fm/venue/dc9-q2xvo
  berhta    https://dice.fm/venue/berhta-8emn5
  songbyrd  https://dice.fm/venue/songbyrd-r58r
  byrdland  https://dice.fm/venue/byrdland-wo3n
"""

from __future__ import annotations
