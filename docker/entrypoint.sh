#!/usr/bin/env bash
set -e

# Configuration
RESOLUTION="${RESOLUTION:-1920x1080x24}"
DISPLAY_NUM="${DISPLAY_NUM:-99}"
PORT="${PORT:-8000}"
ENABLE_VNC="${ENABLE_VNC:-false}"
VNC_PORT="${VNC_PORT:-5900}"

echo "=================================================="
echo "🚀 Starting AgentEverywhereFlow (AEFlow) Headless"
echo "🖥️ Virtual Display: :${DISPLAY_NUM} (${RESOLUTION})"
echo "🌐 API & WebUI Port: ${PORT}"
echo "=================================================="

# 1. Start Xvfb (Virtual Framebuffer)
echo "Starting Xvfb on :${DISPLAY_NUM}..."
Xvfb ":${DISPLAY_NUM}" -screen 0 "${RESOLUTION}" -ac +extension RANDR +extension GLX &
XVFB_PID=$!

export DISPLAY=":${DISPLAY_NUM}"

# Wait for X server to be ready
for i in {1..10}; do
    if xdpyinfo -display ":${DISPLAY_NUM}" >/dev/null 2>&1; then
        echo "✅ Xvfb is ready on :${DISPLAY_NUM}."
        break
    fi
    sleep 0.5
done

# 2. Start lightweight window manager
if command -v openbox >/dev/null 2>&1; then
    echo "Starting Openbox window manager..."
    openbox &
fi

# 3. Optional VNC server for visual debugging / observation
if [ "${ENABLE_VNC}" = "true" ]; then
    echo "📺 Starting x11vnc on port ${VNC_PORT}..."
    x11vnc -display ":${DISPLAY_NUM}" -forever -shared -nopw -rfbport "${VNC_PORT}" &
fi

# 4. Launch AEFlow Dialogue Server
echo "🚀 Launching AEFlow server on 0.0.0.0:${PORT}..."
exec aef serve --host 0.0.0.0 --port "${PORT}"
