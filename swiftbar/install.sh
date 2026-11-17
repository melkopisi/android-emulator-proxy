#!/bin/bash
# Optional menu bar status for the proxy, shown with SwiftBar (https://swiftbar.app).
#
# Both proxy installers call this. It asks first; on "yes" it installs SwiftBar
# (if needed) and the plugin, on "no" it changes nothing. If the plugin is
# already set up it just refreshes it without asking again.
#
#   --yes   install without asking
#   --no    skip without asking

green=$(tput setaf 2 2>/dev/null)
red=$(tput setaf 1 2>/dev/null)
yellow=$(tput setaf 3 2>/dev/null)
reset=$(tput sgr0 2>/dev/null)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN="$SCRIPT_DIR/androidproxy.5s.sh"
PLUGIN_NAME="androidproxy.5s.sh"

swiftbar_installed() {
  [ -d "/Applications/SwiftBar.app" ] || [ -d "$HOME/Applications/SwiftBar.app" ]
}

plugin_dir() {
  local dir
  dir="$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null)"
  echo "${dir/#\~/$HOME}"
}

refresh_swiftbar() {
  if pgrep -xq SwiftBar; then
    open -g "swiftbar://refreshallplugins" 2>/dev/null
  else
    open -ga SwiftBar
  fi
}

# Already set up: keep the link current and refresh, no question needed.
existing_dir="$(plugin_dir)"
if swiftbar_installed && [ -n "$existing_dir" ] && [ -L "$existing_dir/$PLUGIN_NAME" ]; then
  chmod +x "$PLUGIN"
  ln -sf "$PLUGIN" "$existing_dir/$PLUGIN_NAME"
  refresh_swiftbar
  echo "${green}✅ Menu bar status is already set up (refreshed)${reset}"
  exit 0
fi

# Ask.
answer=""
case "$1" in
  --yes|-y) answer="y" ;;
  --no|-n) answer="n" ;;
  *)
    if [ -r /dev/tty ] && { : < /dev/tty; } 2>/dev/null; then
      echo ""
      echo "${yellow}📊 Optional: menu bar status${reset}"
      echo "   Shows the proxy's health live (🟢 healthy · 🔴 stale · 🟠 VPN down · ⚪ off)"
      echo "   with one-click Restart / Stop. Uses the free SwiftBar app."
      if swiftbar_installed; then
        prompt="   Add it to your menu bar? [y/N] "
      else
        prompt="   Install SwiftBar (brew install --cask swiftbar) and add it? [y/N] "
      fi
      read -r -p "$prompt" answer < /dev/tty
    else
      echo "${yellow}ℹ️  Skipping the optional menu bar status (no terminal to ask). Add it later with: $SCRIPT_DIR/install.sh --yes${reset}"
      exit 0
    fi
    ;;
esac

if [[ ! "$answer" =~ ^[Yy] ]]; then
  echo "${yellow}⏭️  Skipped the menu bar status. Add it later with: $SCRIPT_DIR/install.sh${reset}"
  exit 0
fi

# Install SwiftBar if needed.
if ! swiftbar_installed; then
  if ! command -v brew &>/dev/null; then
    echo "${red}❌ Homebrew not found. Install SwiftBar from https://swiftbar.app, then run $SCRIPT_DIR/install.sh${reset}"
    exit 0
  fi
  echo "${yellow}📦 Installing SwiftBar...${reset}"
  if ! brew install --cask swiftbar; then
    echo "${red}❌ Couldn't install SwiftBar. The proxy works without it; retry later with $SCRIPT_DIR/install.sh${reset}"
    exit 0
  fi
fi

# Point SwiftBar at a plugin folder (first run) and link the plugin.
dir="$(plugin_dir)"
if [ -z "$dir" ]; then
  dir="$HOME/.swiftbar"
  defaults write com.ameba.SwiftBar PluginDirectory -string "$dir"
fi
mkdir -p "$dir"
chmod +x "$PLUGIN"
ln -sf "$PLUGIN" "$dir/$PLUGIN_NAME"
refresh_swiftbar
echo "${green}✅ Menu bar status added (plugin linked into $dir)${reset}"
