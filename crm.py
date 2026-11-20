#!/usr/bin/env python3
"""Personal CRM: a local SQLite record of people, built from Apple Contacts,
Messages, Calendar, Apple Mail and (optionally) a LinkedIn Connections.csv export.

Source databases are opened read-only. Message and email text are never copied into
the CRM; they're read live from Messages and Mail when asked for. Everything you add by
hand (notes, relationship labels, cadences, links, merges) survives re-syncs.

The code lives in the kith/ package next to this file; this is the entry point.
Usage: python3 crm.py <command> [args]   (python3 crm.py -h for the list)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kith.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
