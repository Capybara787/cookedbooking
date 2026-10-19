#!/usr/bin/env bash
# Starts Chromium with a debugging port open (this machine only) and its own profile.
# Log in to the CDC portal in the window that opens, then leave it open.

# Snap-packaged Chromium can only write to non-hidden folders under your home,
# so the profile must be ~/cdc_bot_profile (not ~/.something).
PROFILE="$HOME/cdc_bot_profile"

nohup chromium --remote-debugging-port=9222 --user-data-dir="$PROFILE" \
  https://www.cdc.com.sg/ >/dev/null 2>&1 &
