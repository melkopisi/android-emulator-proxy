#!/bin/bash

# Configuration
SOCKS_PORT=1080
PRIVOXY_PORT=8118
PRIVOXY_CONFIG="/tmp/privoxy-android.conf"
LOG_DIR="$HOME/.android-proxy-logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/proxy_$(date +%Y%m%d_%H%M%S).log"

# Colors
green=$(tput setaf 2)
red=$(tput setaf 1)
yellow=$(tput setaf 3)
reset=$(tput sgr0)

# Argument parsing
ATTACH_LIFECYCLE=0
CLEAR_PROXY=0

while [[ "$#" -gt 0 ]]; do
  case $1 in
    -a|--attach) ATTACH_LIFECYCLE=1; shift ;;
    --clear) CLEAR_PROXY=1; shift ;;
    -h|--help)
      echo "Usage: androidproxy [OPTIONS]"
      echo "Options:"
      echo "  -a, --attach   Close the emulator when the proxy is stopped"
      echo "  --clear        Clear the proxy settings from a running emulator"
      echo "  -h, --help     Show this help message"
      exit 0
      ;;
    *) echo "Unknown parameter: $1"; exit 1 ;;
  esac
done

get_running_devices() {
  adb devices | grep -w "device" | awk '{print $1}'
}

if [[ $CLEAR_PROXY -eq 1 ]]; then
  DEVICES=($(get_running_devices))
  if [[ ${#DEVICES[@]} -eq 0 ]]; then
    echo "${red}❌ No running devices found to clear proxy.${reset}"
    exit 1
  elif [[ ${#DEVICES[@]} -eq 1 ]]; then
    TARGET_DEVICE="${DEVICES[0]}"
  else
    echo "${yellow}📱 Select a device to clear proxy:${reset}"
    for i in "${!DEVICES[@]}"; do
      echo "  [$((i+1))] ${DEVICES[$i]}"
    done
    read -p "👉 Select a device [1-${#DEVICES[@]}]: " CHOICE
    if [[ "$CHOICE" =~ ^[0-9]+$ ]] && (( CHOICE > 0 && CHOICE <= ${#DEVICES[@]} )); then
      TARGET_DEVICE="${DEVICES[$((CHOICE-1))]}"
    else
      echo "${red}❌ Invalid choice.${reset}"
      exit 1
    fi
  fi
  echo "${yellow}🧹 Clearing proxy settings on $TARGET_DEVICE...${reset}"
  adb -s "$TARGET_DEVICE" shell settings delete global http_proxy
  adb -s "$TARGET_DEVICE" shell settings delete global global_http_proxy_host
  adb -s "$TARGET_DEVICE" shell settings delete global global_http_proxy_port
  echo "${green}✅ Proxy settings cleared.${reset}"
  exit 0
fi

free_port() {
  local port=$1
  local pids=$(lsof -Pi :$port -sTCP:LISTEN -t 2>/dev/null)
  if [ -n "$pids" ]; then
    echo "${yellow}🧹 Freeing port $port (killing PIDs: $(echo $pids | tr '\n' ' '))...${reset}"
    kill -9 $pids 2>/dev/null
    sleep 1
  fi
}

# Free ports before starting
free_port $SOCKS_PORT
free_port $PRIVOXY_PORT

TARGET_DEVICE=""
EMULATOR_PID=""

DEVICES=($(get_running_devices))
if [[ ${#DEVICES[@]} -eq 1 ]]; then
  TARGET_DEVICE="${DEVICES[0]}"
  echo "${green}✅ Auto-attaching to single running device: $TARGET_DEVICE${reset}"
elif [[ ${#DEVICES[@]} -gt 1 ]]; then
  echo "${yellow}📱 Multiple running devices found. Select one to attach to:${reset}"
  for i in "${!DEVICES[@]}"; do
    echo "  [$((i+1))] ${DEVICES[$i]}"
  done
  echo "  [0] Launch a new emulator"
  read -p "👉 Select an option [0-${#DEVICES[@]}]: " CHOICE
  if [[ "$CHOICE" =~ ^[0-9]+$ ]] && (( CHOICE > 0 && CHOICE <= ${#DEVICES[@]} )); then
    TARGET_DEVICE="${DEVICES[$((CHOICE-1))]}"
    echo "${green}✅ Attaching to running device: $TARGET_DEVICE${reset}"
  elif [[ "$CHOICE" == "0" ]]; then
    # Proceed to launch a new one below
    :
  else
    echo "${red}❌ Invalid choice.${reset}"
    exit 1
  fi
fi

if [[ -z "$TARGET_DEVICE" ]]; then
  echo ""
  echo "${yellow}📱 Available AVDs to launch:${reset}"
  AVDS=($(emulator -list-avds))
  if [[ ${#AVDS[@]} -eq 0 ]]; then
    echo "${red}❌ No running devices and no AVDs found to launch. Exiting.${reset}"
    exit 1
  fi
  for i in "${!AVDS[@]}"; do
    echo "  [$((i+1))] ${AVDS[$i]}"
  done
  echo "  [0] Exit"
  read -p "👉 Select an AVD to launch [0-${#AVDS[@]}]: " CHOICE
  if [[ "$CHOICE" =~ ^[0-9]+$ ]] && (( CHOICE > 0 && CHOICE <= ${#AVDS[@]} )); then
    EMU="${AVDS[$((CHOICE-1))]}"
    echo "🚀 Launching AVD: $EMU"
    
    # Capture current devices to find the new one later
    OLD_DEVICES=($(get_running_devices))
    
    # Enable job control to run emulator in a separate process group
    # so it does not receive SIGINT (Ctrl+C) from the terminal
    set -m
    emulator -avd "$EMU" </dev/null > /dev/null 2>&1 &
    EMULATOR_PID=$!
    set +m
    
    echo "${yellow}⏳ Waiting for emulator to start...${reset}"
    # Wait for a new device to appear
    NEW_DEVICE=""
    while [[ -z "$NEW_DEVICE" ]]; do
      sleep 2
      CURRENT_DEVICES=($(get_running_devices))
      for dev in "${CURRENT_DEVICES[@]}"; do
        found=0
        for old in "${OLD_DEVICES[@]}"; do
          if [[ "$dev" == "$old" ]]; then
            found=1
            break
          fi
        done
        if [[ $found -eq 0 ]]; then
          NEW_DEVICE="$dev"
          break
        fi
      done
    done
    TARGET_DEVICE="$NEW_DEVICE"
    echo "${green}✅ Detected new device: $TARGET_DEVICE${reset}"
    
    echo "${yellow}⏳ Waiting for boot to complete...${reset}"
    while [ "$(adb -s "$TARGET_DEVICE" shell getprop sys.boot_completed | tr -d '\r')" != "1" ]; do 
      sleep 1
    done
  else
    exit 0
  fi
fi

echo "${yellow}🧹 Killing existing standalone proxies (microsocks, privoxy) if any...${reset}"
pkill -f "microsocks -p $SOCKS_PORT" &>/dev/null
pkill -f "privoxy.*$PRIVOXY_CONFIG" &>/dev/null

echo "${green}✅ Starting microsocks on 127.0.0.1:$SOCKS_PORT...${reset}"
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

echo "${green}✅ Starting privoxy on 127.0.0.1:$PRIVOXY_PORT...${reset}"
privoxy --no-daemon "$PRIVOXY_CONFIG" > >(tee -a "$LOG_FILE") 2>&1 &
PRIVOXY_PID=$!

echo "${yellow}⚙️ Setting HTTP proxy on device $TARGET_DEVICE to 10.0.2.2:$PRIVOXY_PORT...${reset}"
adb -s "$TARGET_DEVICE" shell settings put global http_proxy 10.0.2.2:$PRIVOXY_PORT

echo ""
echo "${green}🎉 Proxy is ready and attached to $TARGET_DEVICE!${reset}"
echo "📄 Logging to: $LOG_FILE"
echo "🛑 Press Ctrl+C to stop the proxy and restore device settings."

cleanup() {
  echo ""
  echo "${yellow}🛑 Stopping proxies...${reset}"
  kill $MICROSOCKS_PID $PRIVOXY_PID 2>/dev/null
  free_port $SOCKS_PORT
  free_port $PRIVOXY_PORT
  
  echo "${yellow}🧹 Clearing proxy settings from $TARGET_DEVICE...${reset}"
  adb -s "$TARGET_DEVICE" shell settings delete global http_proxy 2>/dev/null
  adb -s "$TARGET_DEVICE" shell settings delete global global_http_proxy_host 2>/dev/null
  adb -s "$TARGET_DEVICE" shell settings delete global global_http_proxy_port 2>/dev/null
  
  if [[ $ATTACH_LIFECYCLE -eq 1 ]]; then
    echo "${yellow}🛑 Closing emulator $TARGET_DEVICE...${reset}"
    adb -s "$TARGET_DEVICE" emu kill 2>/dev/null || ( [[ -n "$EMULATOR_PID" ]] && kill $EMULATOR_PID 2>/dev/null )
  fi
  
  echo "${green}✅ Teardown complete. Goodbye!${reset}"
  exit 0
}

trap cleanup SIGINT SIGTERM

wait $MICROSOCKS_PID $PRIVOXY_PID
