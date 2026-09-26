#!/usr/bin/env bash
# Install / update omarchy-data-visualization (Omarchy bar widget agf.data-visualization).
#   - plugin (Panel.qml, charts.js, manifest, bin/odv.py) -> ~/.config/omarchy/plugins/agf.data-visualization/
#   - `odv` CLI                                          -> ~/.local/bin/odv (symlink to the installed collector)
# Data: ~/.local/share/omarchy-data-visualization/ (odv.db, snapshot.json, apptime.jsonl, config.json).
# Never leave copies/backups inside ~/.config/omarchy/plugins/ (they shadow the plugin by id).
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
id=agf.data-visualization
dest="$HOME/.config/omarchy/plugins/$id"
units="$HOME/.config/systemd/user"

mkdir -p "$dest/bin" "$units" "$HOME/.local/bin"
cp "$here/plugin/manifest.json" "$here/plugin/Panel.qml" "$here/plugin/charts.js" "$dest/"
cp "$here/collector/odv.py" "$dest/bin/odv.py"
chmod +x "$dest/bin/odv.py"
ln -sfn "$dest/bin/odv.py" "$HOME/.local/bin/odv"
if [ -d "$here/observatory" ]; then   # the Jev Observatory app, when present (not part of the public widget)
  ln -sfn "$here/observatory/jev-observatory" "$HOME/.local/bin/jev-observatory"
  "$here/observatory/install-units.sh"   # socket: the Observatory starts when you open localhost:8765
fi
# Quickshell caches compiled JS (charts.js) and can keep serving the old one after an update
rm -f "$HOME/.cache/quickshell/qmlcache/"*.jsc
echo "installed plugin to $dest"

# No timer: the widget refreshes only when you ask (↻ / R / right-click). Remove units from older installs.
systemctl --user disable --now odv.timer >/dev/null 2>&1 || true
rm -f "$units/odv.timer" "$units/odv.service"
systemctl --user daemon-reload

python3 "$dest/bin/odv.py" run

if ! omarchy-shell shell listShellConfig 2>/dev/null | grep -q "\"$id\""; then
  omarchy plugin enable "$id" >/dev/null 2>&1 || true
  omarchy-shell -q shell moveBarWidget "$id" '{"section":"right"}' || true
fi
echo "done. If the panel shows an old version, run: omarchy-restart-shell"
