#!/bin/bash
# MT5-MCP Runner Script
# Ensures correct environment and working directory

cd /home/ishanp/Documents/GitHub/MT5-mcp
export PYTHONPATH=/home/ishanp/Documents/GitHub/MT5-mcp/src
exec python /home/ishanp/Documents/GitHub/MT5-mcp/tools/mcp_mt5_wrapper.py "$@"
