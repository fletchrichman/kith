"""Readers for each data source. Source databases are opened read-only."""
from kith.sources.calendar import sync_calendar
from kith.sources.contacts import sync_contacts
from kith.sources.mail import sync_mail
from kith.sources.messages import sync_messages


SYNCERS = dict(contacts=sync_contacts, messages=sync_messages, calendar=sync_calendar, mail=sync_mail)
