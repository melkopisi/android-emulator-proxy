#!/bin/bash

# Colors
green=$(tput setaf 2)
red=$(tput setaf 1)
yellow=$(tput setaf 3)
reset=$(tput sgr0)

# Paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/android_mitmproxy.py"
VENV_DIR="$HOME/.local/share/android-mitmproxy/venv"
BIN_DIR="$HOME/.local/bin"
WRAPPER_SCRIPT="$BIN_DIR/androidmitm"
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
for cmd in mitmproxy android-platform-tools; do
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
mkdir -p "$HOME/.local/share/android-mitmproxy"
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
# Automatically generated wrapper for Android MITM Proxy
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
    echo "# Added by Android MITM Proxy" >> "$ZSHRC"
    echo "$PATH_LINE" >> "$ZSHRC"
    echo "${green}✅ Added $BIN_DIR to PATH in $ZSHRC${reset}"
    echo "${yellow}   (Restart your terminal or run 'source ~/.zshrc' for it to take effect)${reset}"
else
    echo "${green}ℹ️  $BIN_DIR is already in your PATH${reset}"
fi

echo ""
echo "${green}🎉 Installer complete. You can now run 'androidmitm' from anywhere to start the proxy.${reset}"
