#!/bin/sh
# Plugin for the Kavita container: loads the panel. Runs from /kavita/wwwroot at start.
tag='<script src="/anilist-sync/panel.js" defer></script>'
grep -q 'anilist-sync/panel.js' index.html && { echo "already loaded"; exit 0; }
grep -q '</body>' index.html || { echo "no </body>, skipped"; exit 0; }
sed -i "s#</body>#$tag</body>#" index.html && echo "panel added"
