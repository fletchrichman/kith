"""Where kith finds its database and the Apple data it reads (each path overridable by environment variable)."""
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the folder holding crm.py
HOME = os.path.expanduser("~")
# Your data lives outside the code, so a plugin update (a fresh copy of this folder) or a re-clone
# keeps it. A crm.db next to crm.py, from older versions of a git checkout, is still used if present.
DATA_DIR = f"{HOME}/Library/Application Support/kith"
DB_PATH = os.environ.get("CRM_DB") or (os.path.join(HERE, "crm.db") if os.path.exists(os.path.join(HERE, "crm.db"))
                                      else os.path.join(DATA_DIR, "crm.db"))
MESSAGES_DB = os.environ.get("CRM_MESSAGES_DB", f"{HOME}/Library/Messages/chat.db")
CONTACTS_GLOB = os.environ.get("CRM_CONTACTS_GLOB",
    f"{HOME}/Library/Application Support/AddressBook/**/AddressBook-v22.abcddb")
CALENDAR_DB = os.environ.get("CRM_CALENDAR_DB",
    f"{HOME}/Library/Group Containers/group.com.apple.calendar/Calendar.sqlitedb")
MAIL_GLOB = os.environ.get("CRM_MAIL_GLOB", f"{HOME}/Library/Mail/V*/MailData/Envelope Index")
AUTO_SYNC_HOURS = 6  # read commands refresh first if the last sync is older than this
APPLE_EPOCH = 978307200  # 2001-01-01 in unix seconds
SETUP_URL = "https://github.com/fletchrichman/kith/blob/main/SETUP.md"
ISSUES_URL = "https://github.com/fletchrichman/kith/issues"
