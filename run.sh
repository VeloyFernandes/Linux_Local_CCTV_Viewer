#!/usr/bin/env bash
# Launch the ONVIF CCTV Monitor as a desktop app.
#
#   ./run.sh                start the monitor (detached — no terminal needed)
#   ./run.sh --install      one-time setup: venv + dependencies + app menu entry
#   ./run.sh --demo         start with synthetic test cameras
#   ./run.sh --foreground   stay attached to this terminal (for debugging)
#
# After `./run.sh --install` the monitor appears in the application menu
# ("ONVIF CCTV Monitor") and starts like any other installed app.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

APP_NAME="ONVIF CCTV Monitor"
DESKTOP_FILE="$HOME/.local/share/applications/onvif-cctv.desktop"
LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/onvif-cctv"

setup_venv() {
    # force = first argument, used by --install to refresh the venv
    if [ ! -x ".venv/bin/python" ]; then
        echo "Creating the virtual environment…"
        python3 -m venv .venv
        .venv/bin/pip install --quiet --upgrade pip
        .venv/bin/pip install --quiet -r requirements.txt
    elif [ "${1:-}" = "force" ]; then
        echo "Reinstalling dependencies…"
        .venv/bin/pip install --quiet --upgrade pip
        .venv/bin/pip install --quiet -r requirements.txt
    elif ! .venv/bin/python -c "import PySide6, cv2, numpy, onvif" 2>/dev/null; then
        echo "Some dependencies are missing — installing…"
        .venv/bin/pip install --quiet -r requirements.txt
    fi
}

install_entry() {
    setup_venv force
    mkdir -p "$HOME/.local/share/applications"
    cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=$APP_NAME
Comment=Watch ONVIF IP cameras
Exec="$PWD/run.sh" --foreground
Icon=video-display
Terminal=false
Categories=Video;Network;
EOF
    echo "Installed “$APP_NAME” to the application menu."
}

ARGS=()
FOREGROUND=0
for arg in "$@"; do
    case "$arg" in
        --install)    install_entry; exit 0 ;;
        --foreground) FOREGROUND=1 ;;
        *)            ARGS+=("$arg") ;;
    esac
done

setup_venv

if [ "$FOREGROUND" = "1" ]; then
    exec .venv/bin/python run.py "${ARGS[@]}"
fi

# Detached: hand the process back to the desktop session and keep a log.
mkdir -p "$LOG_DIR"
nohup .venv/bin/python run.py "${ARGS[@]}" > "$LOG_DIR/app.log" 2>&1 &
