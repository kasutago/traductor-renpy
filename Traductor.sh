#!/bin/sh
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
    echo "No encuentro python3. Instálalo con el gestor de paquetes de tu distribución."
    exit 1
fi
if [ -z "$1" ]; then
    if ! python3 -c "import tkinter" >/dev/null 2>&1; then
        echo "A tu Python le falta tkinter (la parte de ventanas). Instálalo con:"
        echo "  Arch / CachyOS / Manjaro:  sudo pacman -S tk"
        echo "  Debian / Ubuntu / Mint:    sudo apt install python3-tk"
        echo "  Fedora:                    sudo dnf install python3-tkinter"
        echo "O usa el modo sin ventanas:  ./Traductor.sh <ruta del juego>"
        exit 1
    fi
    exec python3 gui.py
else
    python3 traductor.py completo "$1"
fi
