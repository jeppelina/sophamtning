#!/bin/bash
# Installerar hämtningen på den här Macen:
#   1. en egen kopia av repot i ~/Library/Application Support/sophamtning
#      (bakgrundsjobb nekas ofta åtkomst till Dokument-mappen),
#   2. lokalt.env och en Pythonmiljö där,
#   3. launchd-jobbet se.sophamtning.hamta, som körs direkt en gång.
# Kör från repots mapp: bash mac/installera.sh
# Avinstallera: bash mac/installera.sh --ta-bort
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/Library/Application Support/sophamtning"
LOGG="$HOME/Library/Logs/sophamtning.log"
PLIST="$HOME/Library/LaunchAgents/se.sophamtning.hamta.plist"
DOMAIN="gui/$(id -u)"

if [ "${1:-}" = "--ta-bort" ]; then
  launchctl bootout "$DOMAIN" "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Jobbet är borttaget. Kopian ligger kvar i $DEST."
  exit 0
fi

[ -d "$DEST/.git" ] || git clone -q "$(git -C "$SRC" remote get-url origin)" "$DEST"
git -C "$DEST" pull -q --rebase --autostash

if [ ! -f "$DEST/lokalt.env" ]; then
  if [ -f "$SRC/lokalt.env" ]; then
    cp "$SRC/lokalt.env" "$DEST/lokalt.env"
  else
    echo "Skapa först lokalt.env med SOPHAMTNING_ADRESS och NTFY_TOPIC (se README)."
    exit 1
  fi
fi
chmod 600 "$DEST/lokalt.env"

python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install -q -r "$DEST/requirements.txt"

sed -e "s|__KATALOG__|$DEST|g" -e "s|__LOGG__|$LOGG|g" "$SRC/mac/se.sophamtning.hamta.plist" > "$PLIST"
launchctl bootout "$DOMAIN" "$PLIST" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "Installerat och startat. Logg: $LOGG"
