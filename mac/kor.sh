#!/bin/bash
# Körs av launchd (se.sophamtning.hamta) två gånger om dagen och när Macen vaknar:
# hämtar hämtschemat, skickar notis vid ändringar och pushar state.json och
# sophamtning.ics till GitHub. Körs i kopian i ~/Library/Application Support/sophamtning.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
echo "=== $(date '+%Y-%m-%d %H:%M:%S')"

# SOPHAMTNING_ADRESS och NTFY_TOPIC. Filen checkas aldrig in.
if [ ! -f lokalt.env ]; then echo "lokalt.env saknas"; exit 1; fi
set -a; . ./lokalt.env; set +a

git pull -q --rebase --autostash || echo "VARNING: git pull misslyckades"

# Bygg om Pythonmiljön om Homebrew har bytt Python-version under den.
if ! .venv/bin/python -c "import pypdf" 2>/dev/null; then
  echo "Bygger om .venv"
  rm -rf .venv && python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt || exit 1
fi

.venv/bin/python sophamtning.py --hamta
code=$?

for f in state.json sophamtning.ics; do [ -f "$f" ] && git add "$f"; done
if ! git diff --cached --quiet; then
  git commit -q -m "Uppdatera hämtschema"
  git push -q || { git pull -q --rebase && git push -q; } || echo "VARNING: git push misslyckades"
fi
exit $code
