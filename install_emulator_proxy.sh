#!/bin/bash

# Colors
green=$(tput setaf 2)
red=$(tput setaf 1)
yellow=$(tput setaf 3)
reset=$(tput sgr0)

# Paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER_SCRIPT="$SCRIPT_DIR/androidproxy.sh"
SYMLINK_PATH="/usr/local/bin/androidproxy"
EMULATOR_PATH="$HOME/Library/Android/sdk/emulator"

# ✅ Ensure emulator is in PATH
echo "${yellow}🔍 Checking if 'emulator' is in PATH...${reset}"
if ! command -v emulator &>/dev/null; then
  echo "${yellow}🔧 'emulator' not found in PATH. Attempting to add from $EMULATOR_PATH...${reset}"
  if [ -d "$EMULATOR_PATH" ]; then
    if ! grep -q "$EMULATOR_PATH" "$HOME/.zprofile" 2>/dev/null; then
      if [ -s "$HOME/.zprofile" ] && [ -n "$(tail -c1 "$HOME/.zprofile")" ]; then
        echo >> "$HOME/.zprofile"
      fi
      echo "export PATH=\"\$PATH:$EMULATOR_PATH\"" >> "$HOME/.zprofile"
      export PATH="$PATH:$EMULATOR_PATH"
      echo "${green}✅ Added emulator to PATH and updated ~/.zprofile${reset}"
    else
      echo "${yellow}ℹ️ Emulator path already present in ~/.zprofile${reset}"
    fi
  else
    echo "${red}❌ Emulator path not found at $EMULATOR_PATH. Please check your Android SDK installation.${reset}"
  fi
else
  echo "${green}✅ Emulator is already in PATH${reset}"
fi

# ✅ Ensure dependencies
for cmd in privoxy microsocks android-platform-tools; do
  echo "${yellow}🔍 Checking if $cmd is installed...${reset}"
  if ! command -v $cmd &>/dev/null; then
    echo "${yellow}📦 Installing $cmd using Homebrew...${reset}"
    brew install $cmd || { echo "${red}❌ Failed to install $cmd${reset}"; exit 1; }
    echo "${green}✅ Installed $cmd${reset}"
  else
    echo "${green}✅ $cmd is already installed${reset}"
  fi
done

# ✅ Make launcher script executable
if [ -f "$LAUNCHER_SCRIPT" ]; then
  chmod +x "$LAUNCHER_SCRIPT"
  echo "${green}✅ Made $LAUNCHER_SCRIPT executable${reset}"
else
  echo "${red}❌ Launcher script not found at $LAUNCHER_SCRIPT${reset}"
  exit 1
fi

# ✅ Create symlink
echo "${yellow}⚙️ Creating symlink at $SYMLINK_PATH...${reset}"
if [ -L "$SYMLINK_PATH" ] || [ -e "$SYMLINK_PATH" ]; then
  echo "${yellow}🧹 Removing existing file or symlink at $SYMLINK_PATH...${reset}"
  rm "$SYMLINK_PATH" 2>/dev/null || sudo rm "$SYMLINK_PATH"
fi

ln -s "$LAUNCHER_SCRIPT" "$SYMLINK_PATH" 2>/dev/null || sudo ln -s "$LAUNCHER_SCRIPT" "$SYMLINK_PATH"

if [ -L "$SYMLINK_PATH" ]; then
  echo "${green}✅ Symlink created successfully!${reset}"
else
  echo "${red}❌ Failed to create symlink at $SYMLINK_PATH.${reset}"
  exit 1
fi

# Cleanup old alias from .zprofile if it exists
ALIAS_NAME="androidproxy"
if grep -q "alias $ALIAS_NAME=" "$HOME/.zprofile" 2>/dev/null; then
  echo "${yellow}🧹 Removing old alias from ~/.zprofile...${reset}"
  grep -v "alias $ALIAS_NAME=" "$HOME/.zprofile" > "$HOME/.zprofile.tmp" && mv "$HOME/.zprofile.tmp" "$HOME/.zprofile"
  echo "${green}✅ Old alias removed from ~/.zprofile${reset}"
fi

echo ""
echo "${green}🎉 Installer complete. You can now run 'androidproxy' from anywhere to start the proxy.${reset}"
