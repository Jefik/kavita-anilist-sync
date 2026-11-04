#!/bin/sh
# Plugin for the Kavita container: loads the panel. Runs from /kavita/wwwroot at start.
# PANEL_URL must be absolute when Kavita is also opened through another address (a LAN IP, for example).
PANEL_URL="${PANEL_URL:-/anilist-sync/panel.js}"
tag="<script src=\"$PANEL_URL\" defer></script>"
grep -q '</body>' index.html || { echo "no </body>, skipped"; exit 0; }
grep -qF "$tag" index.html && { echo "already loaded"; exit 0; }
sed -i 's#<script src="[^"]*anilist-sync/panel\.js" defer></script>##' index.html
sed -i "s#</body>#$tag</body>#" index.html && echo "panel added from $PANEL_URL"
