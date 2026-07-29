#!/usr/bin/env python3
import argparse
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from InquirerPy import inquirer
from InquirerPy.base.control import Choice
import threading

# --- Monkey-patch InquirerPy to fix terminal resize layout artifacts ---
try:
    from InquirerPy.prompts.fuzzy import FuzzyPrompt
    from InquirerPy.prompts.checkbox import CheckboxPrompt

    _f_init = FuzzyPrompt.__init__
    def _fuzzy_patched(self, *args, **kwargs):
        kwargs.setdefault("max_height", "50%")
        _f_init(self, *args, **kwargs)
    FuzzyPrompt.__init__ = _fuzzy_patched

    _c_init = CheckboxPrompt.__init__
    def _checkbox_patched(self, *args, **kwargs):
        kwargs.setdefault("max_height", "50%")
        _c_init(self, *args, **kwargs)
    CheckboxPrompt.__init__ = _checkbox_patched
except Exception:
    pass
# ----------------------------------------------------------------------

console = Console()

# Configuration
SOCKS_PORT = 1080
PRIVOXY_PORT = 8118
PRIVOXY_CONFIG = "/tmp/privoxy-android.conf"
LOG_DIR = Path.home() / ".android-proxy-logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"proxy_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

# Global states for cleanup
MICROSOCKS_PROC = None
PRIVOXY_PROC = None
ATTACHED_DEVICES = []
ATTACH_LIFECYCLE = False
LAUNCHED_EMULATOR_PIDS = []

def run_cmd(cmd, capture=True):
    """Run a shell command and return stdout. Ignore errors if capture is False."""
    try:
        res = subprocess.run(cmd, shell=True, capture_output=capture, text=True)
        if capture:
            return res.stdout.strip()
    except Exception:
        pass
    return ""

def get_running_devices():
    """Return a list of adb serial numbers for running emulators."""
    out = run_cmd("adb devices")
    devices = []
    for line in out.splitlines():
        if "device" in line and not line.startswith("List"):
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                serial = parts[0]
                if serial.startswith("emulator-"):
                    devices.append(serial)
    return devices

def get_device_name(serial):
    """Attempt to get a human-readable name for an emulator or device."""
    # Try emulator avd name
    name_out = run_cmd(f"adb -s {serial} emu avd name 2>/dev/null")
    if name_out:
        first_line = name_out.splitlines()[0].strip()
        if first_line and first_line != "OK" and "error:" not in first_line:
            return f"{first_line} ({serial})"
    
    # Fallback to product model
    model = run_cmd(f"adb -s {serial} shell getprop ro.product.model 2>/dev/null")
    if model:
        return f"{model} ({serial})"
    return serial

def free_port(port):
    """Kill any process listening on the given port."""
    pids_str = run_cmd(f"lsof -Pi :{port} -sTCP:LISTEN -t 2>/dev/null")
    if pids_str:
        pids = pids_str.replace('\n', ' ')
        console.print(f"[yellow]🧹 Freeing port {port} (killing PIDs: {pids})...[/yellow]")
        run_cmd(f"kill -9 {pids} 2>/dev/null", capture=False)
        time.sleep(1)

def clear_proxy_for_devices(devices):
    """Clear HTTP proxy settings on the given devices."""
    for dev in devices:
        console.print(f"[yellow]🧹 Clearing proxy settings on {get_device_name(dev)}...[/yellow]")
        run_cmd(f"adb -s {dev} shell settings delete global http_proxy")
        run_cmd(f"adb -s {dev} shell settings delete global global_http_proxy_host")
        run_cmd(f"adb -s {dev} shell settings delete global global_http_proxy_port")
        console.print("[green]✅ Proxy settings cleared.[/green]")

def kill_emulators():
    """Attempt to kill attached emulators cleanly."""
    for dev in ATTACHED_DEVICES:
        console.print(f"[yellow]🛑 Closing emulator {dev}...[/yellow]")
        run_cmd(f"adb -s {dev} emu kill 2>/dev/null")
    
    # Fallback to kill any process IDs we launched directly
    if LAUNCHED_EMULATOR_PIDS:
        for pid in LAUNCHED_EMULATOR_PIDS:
            run_cmd(f"kill {pid} 2>/dev/null", capture=False)

def cleanup(signum=None, frame=None):
    """Tear down proxies, clear device settings, and optionally kill emulators."""
    print("\n")
    console.print("[yellow]🛑 Stopping proxies...[/yellow]")
    if MICROSOCKS_PROC:
        MICROSOCKS_PROC.terminate()
    if PRIVOXY_PROC:
        PRIVOXY_PROC.terminate()
        
    free_port(SOCKS_PORT)
    free_port(PRIVOXY_PORT)
    
    for dev in ATTACHED_DEVICES:
        console.print(f"[yellow]🧹 Clearing proxy settings from {get_device_name(dev)}...[/yellow]")
        run_cmd(f"adb -s {dev} shell settings delete global http_proxy 2>/dev/null")
        run_cmd(f"adb -s {dev} shell settings delete global global_http_proxy_host 2>/dev/null")
        run_cmd(f"adb -s {dev} shell settings delete global global_http_proxy_port 2>/dev/null")
        
    if ATTACH_LIFECYCLE:
        kill_emulators()
            
    console.print("[green]✅ Teardown complete. Goodbye![/green]")
    sys.exit(0)

def handle_clear_command():
    """Handle the --clear argument."""
    devices = get_running_devices()
    target_devices = []
    
    if len(devices) == 0:
        console.print("[red]❌ No running devices found to clear proxy.[/red]")
        sys.exit(1)
    elif len(devices) == 1:
        target_devices = [devices[0]]
    else:
        choices = [Choice(dev, name=get_device_name(dev)) for dev in devices]
        console.print("[yellow]📱 Select emulators to clear proxy (Tab to multi-select, Enter to confirm):[/yellow]")
        selected = inquirer.fuzzy(
            message="👉",
            choices=choices,
            multiselect=True
        ).execute()
        
        if not selected:
            console.print("[red]❌ No device selected.[/red]")
            sys.exit(1)
        target_devices = selected
        
    clear_proxy_for_devices(target_devices)
    sys.exit(0)

def main():
    global ATTACH_LIFECYCLE, ATTACHED_DEVICES, MICROSOCKS_PROC, PRIVOXY_PROC, LAUNCHED_EMULATOR_PIDS
    
    parser = argparse.ArgumentParser(description="Android Emulator Proxy")
    parser.add_argument("-a", "--attach", action="store_true", help="Close the emulator when the proxy is stopped")
    parser.add_argument("-s", "--skip", action="store_true", help="Skip emulator launch prompt and start proxy directly")
    parser.add_argument("--clear", action="store_true", help="Clear the proxy settings from a running emulator")
    args = parser.parse_args()
    
    ATTACH_LIFECYCLE = args.attach
    
    if args.clear:
        handle_clear_command()
        
    # Free ports before starting
    free_port(SOCKS_PORT)
    free_port(PRIVOXY_PORT)
    
    devices = get_running_devices()
    
    if len(devices) == 1:
        ATTACHED_DEVICES = [devices[0]]
        console.print(f"[green]✅ Auto-attaching to single running device: {get_device_name(ATTACHED_DEVICES[0])}[/green]")
    elif len(devices) > 1:
        choices = [Choice(dev, name=get_device_name(dev)) for dev in devices]
        console.print("[yellow]📱 Select emulators to attach (Tab to multi-select, Enter to confirm, Esc to launch a new emulator):[/yellow]")
        selected = inquirer.fuzzy(
            message="👉",
            choices=choices,
            multiselect=True
        ).execute()
        
        if selected:
            ATTACHED_DEVICES = selected
            for dev in ATTACHED_DEVICES:
                console.print(f"[green]✅ Attaching to running device: {get_device_name(dev)}[/green]")
    
    if not ATTACHED_DEVICES:
        if args.skip:
            console.print("[yellow]⏭️  Skipping emulator launch due to --skip flag. The proxy will auto-attach to any newly started emulators.[/yellow]")
            selected_avds = ["SKIP"]
        else:
            console.print("")
            console.print("[yellow]📱 Available AVDs to launch:[/yellow]")
            avds_out = run_cmd("emulator -list-avds")
            avds = [line.strip() for line in avds_out.splitlines() if line.strip()]
            
            choices = [Choice("SKIP", name="⏭️  Skip (I will start an emulator manually)")]
            if avds:
                choices.extend([Choice(avd, name=avd) for avd in avds])
                
            selected_avds = inquirer.fuzzy(
                message="👉 Select AVDs to launch (Tab to multi-select, Enter to confirm):",
                choices=choices,
                multiselect=True
            ).execute()
            
            if not selected_avds:
                sys.exit(0)
                
        if "SKIP" in selected_avds:
            if not args.skip:
                console.print("[yellow]⏭️  Skipping emulator launch. The proxy will auto-attach to any newly started emulators.[/yellow]")
        else:
            old_devices = set(get_running_devices())
            
            for avd in selected_avds:
                console.print(f"🚀 Launching AVD: {avd}")
                # Start emulator in background, detaching it so it ignores our Ctrl+C directly
                proc = subprocess.Popen(
                    f"emulator -avd {avd} </dev/null > /dev/null 2>&1",
                    shell=True,
                    preexec_fn=os.setsid
                )
                LAUNCHED_EMULATOR_PIDS.append(proc.pid)
            
            console.print(f"[yellow]⏳ Waiting for {len(selected_avds)} emulator(s) to start...[/yellow]")
            new_devices = []
            expected_count = len(selected_avds)
            
            while len(new_devices) < expected_count:
                time.sleep(2)
                current_devices = set(get_running_devices())
                diff = current_devices - old_devices - set(new_devices)
                for d in diff:
                    new_devices.append(d)
                    console.print(f"[green]✅ Detected new device: {get_device_name(d)}[/green]")
                    
            ATTACHED_DEVICES = new_devices
            
            console.print("[yellow]⏳ Waiting for boot to complete on all devices...[/yellow]")
            for dev in new_devices:
                while True:
                    boot_prop = run_cmd(f"adb -s {dev} shell getprop sys.boot_completed 2>/dev/null").strip()
                    if boot_prop == "1":
                        break
                    time.sleep(1)
                console.print(f"[green]✅ Boot completed for: {get_device_name(dev)}[/green]")

    console.print("[yellow]🧹 Killing existing standalone proxies (microsocks, privoxy) if any...[/yellow]")
    run_cmd(f"pkill -f 'microsocks -p {SOCKS_PORT}'", capture=False)
    run_cmd(f"pkill -f 'privoxy.*{PRIVOXY_CONFIG}'", capture=False)

    console.print(f"[green]✅ Starting microsocks on 127.0.0.1:{SOCKS_PORT}...[/green]")
    with open(LOG_FILE, "a") as logf:
        MICROSOCKS_PROC = subprocess.Popen(
            ["microsocks", "-p", str(SOCKS_PORT)],
            stdout=logf,
            stderr=logf
        )

    config_content = f"""listen-address  127.0.0.1:{PRIVOXY_PORT}
toggle 1
enable-remote-toggle 0
enable-remote-http-toggle 0
enable-edit-actions 0
forward-socks5t / 127.0.0.1:{SOCKS_PORT} .
"""
    with open(PRIVOXY_CONFIG, "w") as f:
        f.write(config_content)

    console.print(f"[green]✅ Starting privoxy on 127.0.0.1:{PRIVOXY_PORT}...[/green]")
    with open(LOG_FILE, "a") as logf:
        PRIVOXY_PROC = subprocess.Popen(
            ["privoxy", "--no-daemon", PRIVOXY_CONFIG],
            stdout=logf,
            stderr=logf
        )

    for dev in ATTACHED_DEVICES:
        console.print(f"[yellow]⚙️ Setting HTTP proxy on device {dev} to 10.0.2.2:{PRIVOXY_PORT}...[/yellow]")
        run_cmd(f"adb -s {dev} shell settings put global http_proxy 10.0.2.2:{PRIVOXY_PORT}")
        console.print(f"[green]🎉 Proxy is ready and attached to {get_device_name(dev)}![/green]")

    console.print(f"📄 Logging to: {LOG_FILE}")
    console.print("🛑 Press Ctrl+C to stop the proxy and restore device settings.")
    
    def device_watcher_thread():
        global ATTACHED_DEVICES
        known_devices = set(ATTACHED_DEVICES)
        while True:
            time.sleep(3)
            current_devices = set(get_running_devices())
            new_devices = current_devices - known_devices
            for dev in new_devices:
                boot_prop = run_cmd(f"adb -s {dev} shell getprop sys.boot_completed 2>/dev/null").strip()
                if boot_prop == "1":
                    console.print(f"\n[green]✅ Auto-attaching proxy to newly started device: {get_device_name(dev)}...[/green]")
                    run_cmd(f"adb -s {dev} shell settings put global http_proxy 10.0.2.2:{PRIVOXY_PORT}")
                    if dev not in ATTACHED_DEVICES:
                        ATTACHED_DEVICES.append(dev)
                    known_devices.add(dev)
                    console.print(f"[green]🎉 Proxy is ready and attached to {get_device_name(dev)}![/green]")
                    
    watcher = threading.Thread(target=device_watcher_thread, daemon=True)
    watcher.start()

    # Wait until interrupted
    try:
        MICROSOCKS_PROC.wait()
        PRIVOXY_PROC.wait()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    try:
        main()
    except KeyboardInterrupt:
        # InquirerPy raises KeyboardInterrupt directly (bypassing SIGINT) when Ctrl+C is pressed at a prompt
        cleanup()
