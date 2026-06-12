#!/bin/bash

green=$(tput setaf 2)
yellow=$(tput setaf 3)
reset=$(tput sgr0)

echo "${yellow}🧹 Cleaning up residuals from the old proxy version...${reset}"

OLD_SCRIPT="$HOME/.android-proxy"
if [ -f "$OLD_SCRIPT" ]; then
  rm "$OLD_SCRIPT"
  echo "${green}✅ Removed old proxy script at $OLD_SCRIPT${reset}"
else
  echo "${yellow}ℹ️ No old proxy script found at $OLD_SCRIPT${reset}"
fi

clean_alias() {
  local file=$1
  if [ -f "$file" ]; then
    if grep -q "alias androidproxy=" "$file" 2>/dev/null; then
      grep -v "alias androidproxy=" "$file" > "${file}.tmp" && mv "${file}.tmp" "$file"
      echo "${green}✅ Removed old alias from $file${reset}"
    fi
  fi
}

clean_alias "$HOME/.zprofile"
clean_alias "$HOME/.bash_profile"
clean_alias "$HOME/.zshrc"
clean_alias "$HOME/.bashrc"

OLD_CONFIG="/tmp/privoxy-android.conf"
if [ -f "$OLD_CONFIG" ]; then
  rm "$OLD_CONFIG"
  echo "${green}✅ Removed old privoxy config at $OLD_CONFIG${reset}"
fi

echo ""
echo "${green}🎉 Cleanup complete!${reset}"
echo "ℹ️  Note: Log files in ~/.android-proxy-logs were kept for your reference."
