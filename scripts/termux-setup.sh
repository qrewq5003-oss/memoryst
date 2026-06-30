#!/data/data/com.termux/files/usr/bin/bash
# Setup memoryst on Termux (Android)
# Run this once: bash scripts/termux-setup.sh

set -e

echo "=== memoryst Termux Setup ==="

# Check if running from external storage (symlinks won't work)
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
case "$SCRIPT_DIR" in
    /storage/emulated/*|/sdcard/*|/mnt/*)
        echo ""
        echo "ERROR: Cannot setup on external storage."
        echo "External storage doesn't support symlinks (needed for Python venv)."
        echo ""
        echo "Fix: Copy project to Termux home first:"
        echo "  cp -r '$SCRIPT_DIR' ~/memoryst"
        echo "  cd ~/memoryst"
        echo "  bash scripts/termux-setup.sh"
        echo ""
        exit 1
        ;;
esac

# Install system dependencies
echo "[1/5] Installing system packages..."
pkg update -y
pkg install -y python git termux-api

# Create venv and install dependencies
echo "[2/5] Installing Python dependencies..."
cd "$(dirname "$0")/.."
python -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# Create data directory
echo "[3/5] Creating data directory..."
mkdir -p data

# Setup .env if not exists
if [ ! -f .env ]; then
    echo "[4/5] Creating .env template..."
    cat > .env << 'EOF'
# Google API keys for embeddings (comma-separated)
GOOGLE_API_KEYS=
GOOGLE_EMBEDDING_MODEL=gemini-embedding-2-preview

# LLM for summaries (NanoGPT or OpenAI-compatible)
LLM_API_BASE=
LLM_API_KEY=
LLM_MODEL=zai-org/glm-4.7

# Server
APP_HOST=0.0.0.0
APP_PORT=8001
API_KEY=
EOF
    echo "  Edit .env with your API keys before starting."
else
    echo "[4/5] .env already exists, skipping."
fi

# Setup autostart
echo "[5/5] Setting up autostart..."
mkdir -p ~/.termux/boot
cp scripts/termux-boot.sh ~/.termux/boot/memoryst.sh
chmod +x ~/.termux/boot/memoryst.sh

echo ""
echo "=== Setup complete ==="
echo ""
echo "To start now:"
echo "  .venv/bin/python -m app.main"
echo ""
echo "To enable autostart on boot:"
echo "  1. Install Termux:Boot from F-Droid"
echo "  2. Open Termux:Boot once to grant permissions"
echo "  3. memoryst will start automatically on boot"
echo ""
echo "Web UI: http://localhost:8000/ui"
echo "API:    http://localhost:8000/docs"
