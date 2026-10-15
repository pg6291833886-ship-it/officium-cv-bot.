#!/bin/sh
set -eu
if [ ! -f bot.py ]; then echo 'ERROR: bot.py is missing; upload the bot source before deployment.' >&2; exit 1; fi
exec python bot.py
