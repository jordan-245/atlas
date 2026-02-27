#!/bin/bash
# Refresh dashboard data — designed for frequent cron runs during market hours.
# generate_data.py fetches live prices from Moomoo (live mode) or yfinance (paper).
set -e
cd /root/atlas
python3 dashboard/generate_data.py 2>&1
# Ensure latest template is deployed
cp -f dashboard/templates/index.html dashboard/data/index.html
