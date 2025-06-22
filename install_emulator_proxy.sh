#!/bin/bash

INSTALLER_SCRIPT="$HOME/.android-proxy"
ALIAS_NAME="androidproxy"
ALIAS_LINE="alias $ALIAS_NAME=\"bash $INSTALLER_SCRIPT\""
EMULATOR_PATH="$HOME/Library/Android/sdk/emulator"
_PATH="$HOME/Library/Android/sdk/emulator"


# Ports and paths
SOCKS_PORT=1080
PRIVOXY_PORT=8118
PRIVOXY_CONFIG="/tmp/privoxy-android.conf"
LOG_DIR="$HOME/.android-proxy-logs"
mkdir -p "$LOG_DIR"

# Colors
green=$(tput setaf 2)
red=$(tput setaf 1)
yellow=$(tput setaf 3)
reset=$(tput sgr0)

# ✅ Ensure emulator is in PATH
echo "${yellow}🔍 Checking if 'emulator' is in PATH...${reset}"
if ! command -v emulator &>/dev/null; then
  echo "${yellow}🔧 'emulator' not found in PATH. Attempting to add from $EMULATOR_PATH...${reset}"
  if [ -d "$EMULATOR_PATH" ]; then
    if ! grep -q "$EMULATOR_PATH" "$HOME/.zprofile" 2>/dev/null; then
      # Add newline if file is not empty and doesn't end with one
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

# ✅ Create the proxy launcher script
echo "${yellow}⚙️ Creating launcher script at $INSTALLER_SCRIPT...${reset}"
cat <<EOL > "$INSTALLER_SCRIPT"
#!/bin/bash

SOCKS_PORT=$SOCKS_PORT
PRIVOXY_PORT=$PRIVOXY_PORT
PRIVOXY_CONFIG="$PRIVOXY_CONFIG"
LOG_DIR="$LOG_DIR"
LOG_FILE="$LOG_DIR/proxy_\$(date +%Y%m%d_%H%M%S).log"
mkdir -p "$LOG_DIR"

echo "$(tput setaf 3)🧹 Killing existing proxies (microsocks, privoxy)...$(tput sgr0)"
pkill -f microsocks &>/dev/null
pkill -f privoxy &>/dev/null

echo "$(tput setaf 2)✅ Starting microsocks on 127.0.0.1:$SOCKS_PORT...$(tput sgr0)"
microsocks -p $SOCKS_PORT > >(tee -a "$LOG_FILE") 2>&1 &
MICROSOCKS_PID=$!

cat <<EOC > "$PRIVOXY_CONFIG"
listen-address  127.0.0.1:$PRIVOXY_PORT
toggle 1
enable-remote-toggle 0
enable-remote-http-toggle 0
enable-edit-actions 0
forward-socks5t / 127.0.0.1:$SOCKS_PORT .
EOC

echo "$(tput setaf 2)✅ Starting privoxy on 127.0.0.1:$PRIVOXY_PORT...$(tput sgr0)"
privoxy --no-daemon "$PRIVOXY_CONFIG" > >(tee -a "$LOG_FILE") 2>&1 &
PRIVOXY_PID=$!

# AVD selection
echo "\n$(tput setaf 3)📱 Available AVDs:$(tput sgr0)"
AVDS=($(emulator -list-avds))
if [[ ${#AVDS[@]} -eq 0 ]]; then
  echo "❌ No AVDs found. Skipping launch."
else
  for i in "${!AVDS[@]}"; do
    echo "  [$((i+1))] ${AVDS[$i]}"
  done
  echo "  [0] None"
  read -p "👉 Select an emulator to launch [0-${#AVDS[@]}]: " CHOICE
  if [[ "$CHOICE" =~ ^[0-9]+$ ]] && (( CHOICE > 0 && CHOICE <= ${#AVDS[@]} )); then
    EMU="${AVDS[$((CHOICE-1))]}"
    echo "🚀 Launching AVD: $EMU"
    nohup emulator -avd "$EMU" > /dev/null 2>&1 &
  fi
fi

while [ "`adb shell getprop sys.boot_completed | tr -d '\r' `" != "1" ] ; do sleep 1; done
adb wait-for-device
adb shell settings put global http_proxy 10.0.2.2:$PRIVOXY_PORT

echo ""
echo "$(tput setaf 2)🎉 Proxy is ready!$(tput sgr0)"
echo "➡️ $(tput setaf 3)Set Android emulator Wi-Fi proxy to:$(tput sgr0)"
echo "    Host: 10.0.2.2"
echo "    Port: $PRIVOXY_PORT"
echo "📄 Logging to: $LOG_FILE"
echo "🛑 Press Ctrl+C to stop everything."



trap "echo '🛑 Stopping proxies...'; kill $MICROSOCKS_PID $PRIVOXY_PID; exit 0" SIGINT

wait
EOL

chmod +x "$INSTALLER_SCRIPT"
echo "${green}✅ Launcher script created${reset}"

# ✅ Add alias if not already present
echo "${yellow}🔍 Checking for alias in ~/.zprofile...${reset}"
if ! grep -Fxq "$ALIAS_LINE" "$HOME/.zprofile" 2>/dev/null; then
  # Add newline if file is not empty and doesn't end with one
  if [ -s "$HOME/.zprofile" ] && [ -n "$(tail -c1 "$HOME/.zprofile")" ]; then
    echo >> "$HOME/.zprofile"
  fi
  echo "$ALIAS_LINE" >> "$HOME/.zprofile"
  echo "${green}✅ Alias '$ALIAS_NAME' added to ~/.zprofile${reset}"
else
  echo "${yellow}ℹ️ Alias already present in ~/.zprofile${reset}"
fi

# ✅ Source updated .zprofile
echo "${yellow}📦 Sourcing ~/.zprofile to update shell environment...${reset}"
source "$HOME/.zprofile"

echo ""
echo "${green}✅ Installer complete. Run '${ALIAS_NAME}' to start the proxy anytime.${reset}"
