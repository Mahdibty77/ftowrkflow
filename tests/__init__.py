"""Test suite for the Foolad Tabar Workflow platform.

The most important thing in here is ``tests/snapshot.py``. This project carries
a large amount of business logic that is not covered by unit tests and that must
not change: the FTCO coding rules, the price calculation, the Jalali calendar,
the document-number format, and above all the routing rules that decide which
unit and which role may do what to a case.

``snapshot.py`` pins all of that down by *executing* it against a fixed set of
inputs and writing the answers to a JSON file. Two checkouts that produce the
same file behave identically on every input the snapshot covers. That is what
makes it safe to refactor: run it before a change, run it after, diff the two.

See ``snapshot.py`` for how to run it.
"""
