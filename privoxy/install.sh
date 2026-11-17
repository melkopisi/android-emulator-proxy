#!/bin/bash

# Colors
green=$(tput setaf 2)
red=$(tput setaf 1)
yellow=$(tput setaf 3)
reset=$(tput sgr0)

# Paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/androidproxy.py"
VENV_DIR="$HOME/.local/share/android-emulator-proxy/venv"
BIN_DIR="$HOME/.local/bin"
WRAPPER_SCRIPT="$BIN_DIR/androidproxy"
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

# ✅ Ensure brew dependencies
for cmd in privoxy microsocks android-platform-tools; do
  echo "${yellow}🔍 Checking if $cmd is installed...${reset}"
  if [[ "$cmd" == "android-platform-tools" ]]; then
    check_cmd="adb"
  else
    check_cmd="$cmd"
  fi
  if ! command -v $check_cmd &>/dev/null; then
    echo "${yellow}📦 Installing $cmd using Homebrew...${reset}"
    brew install $cmd || { echo "${red}❌ Failed to install $cmd${reset}"; exit 1; }
    echo "${green}✅ Installed $cmd${reset}"
  else
    echo "${green}✅ $cmd is already installed${reset}"
  fi
done

# ✅ Setup Python Environment
echo "${yellow}📦 Setting up isolated Python environment (venv)...${reset}"
mkdir -p "$HOME/.local/share/android-emulator-proxy"
python3 -m venv "$VENV_DIR"

echo "${yellow}📦 Installing Python dependencies securely...${reset}"
"$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt" --quiet

# ✅ Make python script executable
chmod +x "$PYTHON_SCRIPT"

# ✅ Create wrapper script
echo "${yellow}🔗 Creating command wrapper in $BIN_DIR...${reset}"
mkdir -p "$BIN_DIR"

cat > "$WRAPPER_SCRIPT" << EOF
#!/usr/bin/env bash
# Automatically generated wrapper for Android Emulator Proxy
VENV_PYTHON="$VENV_DIR/bin/python"
SCRIPT_PATH="$PYTHON_SCRIPT"
exec "\$VENV_PYTHON" "\$SCRIPT_PATH" "\$@"
EOF

chmod +x "$WRAPPER_SCRIPT"
echo "${green}✅ Wrapper script created at $WRAPPER_SCRIPT${reset}"

# ✅ Ensure ~/.local/bin is in PATH
ZSHRC="$HOME/.zshrc"
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'

if ! grep -qF 'export PATH="$HOME/.local/bin:$PATH"' "$ZSHRC" 2>/dev/null; then
    echo "" >> "$ZSHRC"
    echo "# Added by Android Emulator Proxy" >> "$ZSHRC"
    echo "$PATH_LINE" >> "$ZSHRC"
    echo "${green}✅ Added $BIN_DIR to PATH in $ZSHRC${reset}"
    echo "${yellow}   (Restart your terminal or run 'source ~/.zshrc' for it to take effect)${reset}"
else
    echo "${green}ℹ️  $BIN_DIR is already in your PATH${reset}"
fi

# Cleanup old stuff
if [ -L "/usr/local/bin/androidproxy" ]; then
    echo "${yellow}🧹 Removing old symlink from /usr/local/bin/androidproxy...${reset}"
    rm "/usr/local/bin/androidproxy" 2>/dev/null || sudo rm "/usr/local/bin/androidproxy" 2>/dev/null
fi

if grep -q "alias androidproxy=" "$HOME/.zprofile" 2>/dev/null; then
  echo "${yellow}🧹 Removing old alias from ~/.zprofile...${reset}"
  grep -v "alias androidproxy=" "$HOME/.zprofile" > "$HOME/.zprofile.tmp" && mv "$HOME/.zprofile.tmp" "$HOME/.zprofile"
fi

# ✅ Ask which URL to health-check (Enter keeps the default)
"$WRAPPER_SCRIPT" config check-url --ask

# ✅ Optional menu bar status (SwiftBar)
"$SCRIPT_DIR/../swiftbar/install.sh"

echo ""
echo "${green}🎉 Installer complete. You can now run 'androidproxy' from anywhere to start the proxy (or 'up' to run it in the background).${reset}"
