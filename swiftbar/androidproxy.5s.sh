#!/usr/bin/env bash
# <xbar.title>Android Emulator Proxy</xbar.title>
# <xbar.desc>Live health of the Android emulator proxy: stale proxy vs VPN down, restart in one click.</xbar.desc>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
#
# Refreshes every 5s (from the filename). It only reads the state file the
# proxy writes, so it is cheap: the proxy itself runs the health checks.

for cmd in "$HOME/.local/bin/androidproxy" "$HOME/.local/bin/androidmitm"; do
  if [ -x "$cmd" ]; then
    exec "$cmd" swiftbar
  fi
done

echo "⚠️"
echo "---"
echo "androidproxy / androidmitm are not installed"
