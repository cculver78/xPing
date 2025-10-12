
#!/usr/bin/env bash
set -euo pipefail

# xPing Linux build script
# - Builds both CLI (xping.py) and GUI (xping_gui.py) with PyInstaller
# - Installs binaries under ~/xPing
# - Creates a .desktop launcher for the GUI only

# -------- Paths & names --------
PROJECT_DIR="$(pwd)"
APP_DIR="$HOME/xPing"
APP_NAME_GUI="xPing"
BIN_CLI_NAME="xping"
BIN_GUI_NAME="xping-gui"
ICON_SRC="$PROJECT_DIR/icon.png"           # optional; copied if present
ICON_DST="$APP_DIR/icon.png"
DESKTOP_FILE="$HOME/Desktop/${APP_NAME_GUI}.desktop"

# -------- Build --------
echo "[xPing] Cleaning previous build artifacts..."
rm -rf build dist *.spec || true

echo "[xPing] Building CLI: ${BIN_CLI_NAME}"
pyinstaller \
  --clean \
  --noconfirm \
  --onefile \
  --name "${BIN_CLI_NAME}" \
  "./xping.py"

echo "[xPing] Building GUI: ${BIN_GUI_NAME}"
pyinstaller \
  --clean \
  --noconfirm \
  --onefile \
  --windowed \
  --name "${BIN_GUI_NAME}" \
  "./xping_gui.py"

# -------- Install --------
echo "[xPing] Installing to ${APP_DIR}"
mkdir -p "${APP_DIR}"

# Remove old binaries if they exist
[ -f "${APP_DIR}/${BIN_CLI_NAME}" ] && rm -f "${APP_DIR}/${BIN_CLI_NAME}"
[ -f "${APP_DIR}/${BIN_GUI_NAME}" ] && rm -f "${APP_DIR}/${BIN_GUI_NAME}"

# Copy new binaries
cp "dist/${BIN_CLI_NAME}" "${APP_DIR}/"
cp "dist/${BIN_GUI_NAME}" "${APP_DIR}/"

# Copy icon if available
if [[ -f "${ICON_SRC}" ]]; then
  cp "${ICON_SRC}" "${ICON_DST}"
  echo "[xPing] Icon copied to ${ICON_DST}"
else
  echo "[xPing] No icon.png found in project root; desktop entry will reference system theme or be icon-less."
fi

# Ensure executables
chmod +x "${APP_DIR}/${BIN_CLI_NAME}" "${APP_DIR}/${BIN_GUI_NAME}" || true

# -------- Cleanup --------
echo "[xPing] Cleaning build folders and spec files..."
rm -f "${PROJECT_DIR}/${BIN_CLI_NAME}.spec" "${PROJECT_DIR}/${BIN_GUI_NAME}.spec" || true
rm -rf build dist || true

# -------- Desktop entry (GUI only) --------
echo "[xPing] Creating desktop launcher for GUI..."
# Use absolute paths in .desktop (tilde is not reliably expanded by all launchers)
EXEC_PATH="${APP_DIR}/${BIN_GUI_NAME}"
ICON_PATH="${ICON_DST}"
COMMENT_TXT="xPing GUI — realtime multi-host ping monitor"

cat << EOF > "${DESKTOP_FILE}"
[Desktop Entry]
Version=1.0
Type=Application
Name=${APP_NAME_GUI}
Exec=${EXEC_PATH}
Icon=${ICON_PATH}
Path=${APP_DIR}
Comment=${COMMENT_TXT}
Categories=Network;Utility;
Terminal=false
EOF

chmod +x "${DESKTOP_FILE}"
echo "[xPing] .desktop file created at: ${DESKTOP_FILE}"

echo "[xPing] Done. Binaries installed to ${APP_DIR}."
