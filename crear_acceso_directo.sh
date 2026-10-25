#!/bin/sh
# Crea el acceso directo "Traductor Ren'Py" en el menú de aplicaciones de Linux.
# Así la app se abre sin la ventana negra de la terminal. Vuelve a ejecutarlo si
# mueves esta carpeta. Para quitarlo: rm ~/.local/share/applications/traductor-renpy.desktop
cd "$(dirname "$0")" || exit 1
DIR="$(pwd)"
DEST="$HOME/.local/share/applications/traductor-renpy.desktop"
mkdir -p "$(dirname "$DEST")"
cat > "$DEST" <<FIN
[Desktop Entry]
Type=Application
Name=Traductor Ren'Py
Comment=Traduce juegos de Ren'Py al español con MarianMT
Exec=python3 "$DIR/gui.py"
Path=$DIR
Icon=accessories-dictionary
Terminal=false
Categories=Utility;
FIN
chmod +x "$DEST"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$(dirname "$DEST")" >/dev/null 2>&1
echo "Listo: busca \"Traductor Ren'Py\" en el menú de aplicaciones."
