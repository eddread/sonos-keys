#!/bin/bash
# Setup script for Sonos Keys
set -e

cd "$(dirname "$0")"

echo "Creating virtual environment..."
python3 -m venv .venv

echo "Installing dependencies..."
.venv/bin/pip install -r requirements.txt

echo ""
echo "Done! To run:"
echo "  cd sonos-keys && .venv/bin/python sonos_keys.py"
echo ""
echo "IMPORTANT: You need to grant Accessibility permissions to your terminal"
echo "  System Settings -> Privacy & Security -> Accessibility"
echo "  Add your terminal app (Terminal.app, iTerm2, etc.)"
