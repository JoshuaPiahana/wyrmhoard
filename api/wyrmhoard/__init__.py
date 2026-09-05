"""
wyrmhoard - a household financial picture, assembled from your own records.

A wyrm knows its hoard to the last coin. It does not guess, it is not
surprised, and nothing goes missing without being noticed.

That is the whole idea here: gather the scattered records of a household's
money into one place, count them honestly, and make the picture clear enough
to act on. The gold is not the point. Knowing exactly what you have is.

Design rules this codebase holds to:

  1. Never invent a number. If the data does not support a figure, say so.
     A confident wrong number is worse than an acknowledged gap.
  2. Never phone home. No telemetry, no cloud, no third-party API calls.
  3. Never shame. The output is read by a family, including children. It
     reports what is true and what would help, and it does not editorialise
     about past decisions.
  4. Degrade loudly. If categorisation coverage is poor or entitlement rates
     are unverified, the dashboard says so instead of drawing a clean chart
     over a shaky foundation.
"""

__version__ = "0.1.0"
