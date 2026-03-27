#!/bin/bash
if [ -f /tmp/ez-trading.pids ]; then
    kill $(cat /tmp/ez-trading.pids) 2>/dev/null
    rm /tmp/ez-trading.pids
    echo "ez-trading stopped."
else
    echo "No running instance found."
fi
