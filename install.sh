#!/usr/bin/env bash
# Sets up the personal CRM on this Mac: checks requirements, installs the Claude Code
# skill pointing at this folder, and runs the first sync. Safe to re-run.
# Usage: ./install.sh [--no-sync]
set -euo pipefail

CRM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/crm"
DO_SYNC=1
[[ "${1:-}" == "--no-sync" ]] && DO_SYNC=0

say() { printf '\n==> %s\n' "$1"; }

if [[ "$(uname)" != "Darwin" ]]; then
  echo "This CRM reads Apple Contacts, Messages, Calendar and Mail, so it only runs on macOS." >&2
  exit 1
fi

if ! command -v python3 >/dev/null; then
  echo "python3 not found. Install the Xcode Command Line Tools: xcode-select --install" >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
  echo "Python 3.9 or newer is required (found $(python3 --version 2>&1))." >&2
  exit 1
fi

say "Installing the Claude Code skill to $SKILL_DIR"
mkdir -p "$SKILL_DIR"
tmp="$(mktemp)"
# The skill is written for the plugin; point it at this checkout instead.
sed "s|\${CLAUDE_PLUGIN_ROOT}|$CRM_DIR|g" "$CRM_DIR/skills/crm/SKILL.md" > "$tmp"
if [[ -f "$SKILL_DIR/SKILL.md" ]] && ! cmp -s "$tmp" "$SKILL_DIR/SKILL.md"; then
  backup="$SKILL_DIR/SKILL.md.bak-$(date +%Y%m%d%H%M%S)"
  cp "$SKILL_DIR/SKILL.md" "$backup"
  echo "Existing skill backed up to $backup"
fi
mv "$tmp" "$SKILL_DIR/SKILL.md"
echo "Skill points at $CRM_DIR"
if grep -q '"kith@' "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/installed_plugins.json" 2>/dev/null; then
  echo "Note: the kith plugin is installed too, so Claude sees two copies of the skill. Keep one:"
  echo "  claude plugin uninstall kith@kith    or    rm -r \"$SKILL_DIR\""
fi

if [[ $DO_SYNC == 1 ]]; then
  say "Running the first sync (reads your Mac's data; nothing leaves this machine)"
  if ! python3 "$CRM_DIR/crm.py" sync; then
    echo "Sync hit an error. See the Full Disk Access step below, then run: python3 \"$CRM_DIR/crm.py\" sync"
  fi
fi

cat <<EOF

==> Next steps
1. Full Disk Access: if any source above says FAILED or mentions Full Disk Access, open
   System Settings > Privacy & Security > Full Disk Access and turn it on for the app you
   run Claude Code from (Terminal, iTerm, VS Code, or Claude). Quit and reopen that app,
   then run:  python3 "$CRM_DIR/crm.py" sync
2. Your email addresses: run  python3 "$CRM_DIR/crm.py" me
   and add any address that's missing:  python3 "$CRM_DIR/crm.py" me --add you@work.com
3. LinkedIn (optional): LinkedIn > Settings > Data privacy > Get a copy of your data >
   Connections. When the email arrives, unzip it and run:
   python3 "$CRM_DIR/crm.py" sync --linkedin ~/Downloads/Connections.csv
4. Try it: open Claude Code anywhere and ask "who do I know at Stripe?"

Something missing (old texts, work calendar, Gmail)? Step-by-step setup for every source:
  $CRM_DIR/SETUP.md
EOF
