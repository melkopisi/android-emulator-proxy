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
MITMPROXY_PORT = 8080
LOG_DIR = Path.home() / ".android-proxy-logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"mitmproxy_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

# Global states for cleanup
ATTACHED_DEVICES = []
ATTACH_LIFECYCLE = False
LAUNCHED_EMULATOR_PIDS = []
CLEANUP_DONE = False

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
    """Return a list of adb serial numbers for running devices."""
    out = run_cmd("adb devices")
    devices = []
    for line in out.splitlines():
        if "device" in line and not line.startswith("List"):
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])
    return devices

def get_device_name(serial):
    """Attempt to get a human-readable name for an emulator or device."""
    name_out = run_cmd(f"adb -s {serial} emu avd name 2>/dev/null")
    if name_out:
        first_line = name_out.splitlines()[0].strip()
        if first_line and first_line != "OK" and "error:" not in first_line:
            return f"{first_line} ({serial})"
    
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
    
    if LAUNCHED_EMULATOR_PIDS:
        for pid in LAUNCHED_EMULATOR_PIDS:
            run_cmd(f"kill {pid} 2>/dev/null", capture=False)

def cleanup(signum=None, frame=None):
    """Tear down proxies, clear device settings, and optionally kill emulators."""
    global CLEANUP_DONE
    if CLEANUP_DONE:
        return
    CLEANUP_DONE = True
    
    print("\n")
    console.print("[yellow]🛑 Cleaning up and restoring settings...[/yellow]")
    
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
    global ATTACH_LIFECYCLE, ATTACHED_DEVICES, LAUNCHED_EMULATOR_PIDS
    
    parser = argparse.ArgumentParser(description="Android Emulator mitmproxy")
    parser.add_argument("-a", "--attach", action="store_true", help="Close the emulator when the proxy is stopped")
    parser.add_argument("--clear", action="store_true", help="Clear the proxy settings from a running emulator")
    parser.add_argument("-f", "--filters", nargs="*", default=[], help="Filters to apply in mitmproxy (e.g., example.com anotherdomain.com)")
    parser.add_argument("-w", "--web", action="store_true", help="Open mitmproxy in the web browser (mitmweb)")
    args = parser.parse_args()
    
    ATTACH_LIFECYCLE = args.attach
    
    if args.clear:
        handle_clear_command()
        
    # Free ports before starting
    free_port(MITMPROXY_PORT)
    
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
        console.print("")
        console.print("[yellow]📱 Available AVDs to launch:[/yellow]")
        avds_out = run_cmd("emulator -list-avds")
        avds = [line.strip() for line in avds_out.splitlines() if line.strip()]
        
        if not avds:
            console.print("[red]❌ No running devices and no AVDs found to launch. Exiting.[/red]")
            sys.exit(1)
            
        choices = [Choice(avd, name=avd) for avd in avds]
        
        selected_avds = inquirer.fuzzy(
            message="👉 Select AVDs to launch (Tab to multi-select, Enter to confirm):",
            choices=choices,
            multiselect=True
        ).execute()
        
        if not selected_avds:
            sys.exit(0)
            
        old_devices = set(get_running_devices())
        
        for avd in selected_avds:
            console.print(f"🚀 Launching AVD: {avd}")
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

    console.print(f"[yellow]🧹 Killing existing mitmproxy instances on port {MITMPROXY_PORT}...[/yellow]")
    run_cmd(f"pkill -f 'mitmproxy.*{MITMPROXY_PORT}'", capture=False)

    for dev in ATTACHED_DEVICES:
        console.print(f"[yellow]⚙️ Setting HTTP proxy on device {dev} to 10.0.2.2:{MITMPROXY_PORT}...[/yellow]")
        run_cmd(f"adb -s {dev} shell settings put global http_proxy 10.0.2.2:{MITMPROXY_PORT}")
        console.print(f"[green]🎉 Proxy attached to {get_device_name(dev)}![/green]")

    executable = "mitmweb" if args.web else "mitmproxy"
    mitm_cmd = [
        executable,
        "--listen-port", str(MITMPROXY_PORT),
        "--showhost",
        "--set", "console_eventlog_verbosity=error",
        "--set", "termlog_verbosity=error",
        "--set", "ssl_insecure=true",
        "--set", "web_password=admin",
        "--set", "connection_strategy=lazy",
        "--set", "stream_large_bodies=10m",
        "--set", "keep_host_header=true",
        "--set", "timeout=300"
    ]

    if args.filters:
        filter_expr = " | ".join(args.filters)
        mitm_cmd.extend(["--set", f"view_filter={filter_expr}"])

    console.print(f"[green]✅ Starting {executable}...[/green]")
    if args.web:
        console.print("🛑 [bold red]Press Ctrl+C in this terminal to stop and restore device settings.[/bold red]")
    else:
        console.print("🛑 [bold red]Press 'q' then 'y' inside mitmproxy to stop and restore device settings.[/bold red]")
    time.sleep(2) # brief pause to read the message before mitmproxy takes over
    
    try:
        subprocess.run(mitm_cmd)
    except Exception as e:
        console.print(f"[red]Error running mitmproxy: {e}[/red]")
    finally:
        cleanup()

if __name__ == "__main__":
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    try:
        main()
    except KeyboardInterrupt:
        cleanup()
