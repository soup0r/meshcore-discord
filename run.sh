#!/bin/bash
# Quick start script for MeshCore Discord Bridge

set -e

echo "MeshCore to Discord Bridge - Setup & Run"
echo "========================================"
echo ""

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Please install Python 3.8+"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
echo "✓ Found Python $PYTHON_VERSION"

# Check config file
if [ ! -f "config.yaml" ]; then
    echo "❌ config.yaml not found!"
    echo ""
    echo "Please create config.yaml with your settings:"
    echo ""
    cat << 'EOF'
meshcore:
  host: "192.168.1.100"
  port: 4000

discord:
  token: "YOUR_BOT_TOKEN_HERE"
  channels:
    messages: 1234567890
    info: 9876543210

logging:
  level: "INFO"
  file: "meshcore_discord.log"
EOF
    echo ""
    exit 1
fi

echo "✓ Found config.yaml"

# Setup virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo ""
    echo "Creating virtual environment..."
    python3 -m venv venv
    echo "✓ Virtual environment created"
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install/update dependencies
echo ""
echo "Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo "✓ Dependencies installed"

# Run the bridge
echo ""
echo "========================================"
echo "Starting MeshCore Discord Bridge..."
echo "Press Ctrl+C to stop"
echo "========================================"
echo ""

python main.py
