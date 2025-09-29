#!/bin/bash

# LNbits startup script
# Function: Pull latest code and start LNbits service

echo "Starting LNbits..."

# 1. Stop existing LNbits processes
echo "Stopping existing LNbits processes..."
pkill -f "lnbits" || echo "No existing LNbits processes found"

# Wait a moment for processes to stop
sleep 2

# 2. Pull latest code
echo "Pulling latest code..."
git pull

# 3. Start LNbits service
echo "Starting LNbits on port 5000..."
nohup poetry run lnbits --port 5000 --host 0.0.0.0 > lnbits.log 2>&1 &

# Get process ID
PID=$!
echo "LNbits started with PID: $PID"
echo "Log file: lnbits.log"
echo "Access URL: http://0.0.0.0:5000"

# Wait a few seconds to check if service started normally
sleep 3

# Check if process is still running
if ps -p $PID > /dev/null; then
    echo "✅ LNbits is running successfully!"
    echo "To stop the service, run: kill $PID"
else
    echo "❌ LNbits failed to start. Check lnbits.log for details."
    exit 1
fi
