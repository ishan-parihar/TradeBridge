#!/bin/bash
# TradeBridge Runner Script
# Ensures correct environment and working directory

cd /home/ishanp/Documents/GitHub/TradeBridge
export PYTHONPATH=/home/ishanp/Documents/GitHub/TradeBridge/src
exec python /home/ishanp/Documents/GitHub/TradeBridge/tools/mcp_mt5_wrapper.py "$@"
