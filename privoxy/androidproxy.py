#!/usr/bin/env python3
import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
import proxyctl  # noqa: E402
from proxyctl import free_port, get_device_name, get_running_devices, run_cmd  # noqa: E402

# Configuration
SOCKS_PORT = 1080
PRIVOXY_PORT = 8118
PRIVOXY_CONFIG = proxyctl.STATE_DIR / "privoxy.conf"

PRIVOXY_CONFIG_TEMPLATE = """listen-address  127.0.0.1:{privoxy_port}
toggle 1
enable-remote-toggle 0
enable-remote-http-toggle 0
enable-edit-actions 0
forward-socks5t / 127.0.0.1:{socks_port} .
# Don't hold on to upstream connections: when the VPN tunnel reconnects,
# pooled connections die silently and requests hang until a restart.
keep-alive-timeout 5
default-server-timeout 5
connection-sharing 0
socket-timeout 30
"""


def start_services(logf):
    PRIVOXY_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    PRIVOXY_CONFIG.write_text(PRIVOXY_CONFIG_TEMPLATE.format(privoxy_port=PRIVOXY_PORT, socks_port=SOCKS_PORT))
    microsocks = subprocess.Popen(["microsocks", "-i", "127.0.0.1", "-p", str(SOCKS_PORT)],
                                  stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT)
    privoxy = subprocess.Popen(["privoxy", "--no-daemon", str(PRIVOXY_CONFIG)],
                               stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT)
    return [microsocks, privoxy]


def local_check():
    return proxyctl.port_open(PRIVOXY_PORT) and proxyctl.socks5_handshake_ok(SOCKS_PORT)


def build_variant(_args=()):
    return proxyctl.Variant(
        name="privoxy",
        command="androidproxy",
        port=PRIVOXY_PORT,
        ports=[SOCKS_PORT, PRIVOXY_PORT],
        start_services=start_services,
        local_check=local_check,
    )


# Control commands (up/status/restart/stop/...) are handled before the heavy
# UI imports so the menu bar, which runs `status` every few seconds, stays fast.
if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] in proxyctl.SUBCOMMANDS:
    sys.exit(proxyctl.cli(build_variant, __file__, sys.argv[1:]))

from rich.console import Console  # noqa: E402
from InquirerPy import inquirer  # noqa: E402
from InquirerPy.base.control import Choice  # noqa: E402

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

# Global states for cleanup
ATTACHED_DEVICES = []
ATTACH_LIFECYCLE = False
LAUNCHED_EMULATOR_PIDS = []
SUPERVISOR = None


def clear_proxy_for_devices(devices):
    """Clear HTTP proxy settings on the given devices."""
    for dev in devices:
        console.print(f"[yellow]🧹 Clearing proxy settings on {get_device_name(dev)}...[/yellow]")
        proxyctl.clear_device_proxy(dev)
        console.print("[green]✅ Proxy settings cleared.[/green]")


def kill_emulators():
    """Attempt to kill attached emulators cleanly."""
    for dev in ATTACHED_DEVICES:
        console.print(f"[yellow]🛑 Closing emulator {dev}...[/yellow]")
        run_cmd(f"adb -s {dev} emu kill 2>/dev/null")

    # Fallback to kill any process IDs we launched directly
    for pid in LAUNCHED_EMULATOR_PIDS:
        run_cmd(f"kill {pid} 2>/dev/null", capture=False)


def cleanup(signum=None, frame=None):
    """Ctrl+C before the proxy is up: just leave (the proxy owns teardown once it runs)."""
    if SUPERVISOR is not None:
        SUPERVISOR.request_stop()
        return
    print("\n")
    if ATTACH_LIFECYCLE:
        kill_emulators()
    console.print("[green]✅ Nothing to tear down. Goodbye![/green]")
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

    if proxyctl.instance_running():
        console.print("[yellow]ℹ️  The proxy is running and re-applies its setting to attached emulators. "
                      "Use `androidproxy stop` to detach for good.[/yellow]")
    clear_proxy_for_devices(target_devices)
    sys.exit(0)


def main():
    global ATTACH_LIFECYCLE, ATTACHED_DEVICES, LAUNCHED_EMULATOR_PIDS, SUPERVISOR

    parser = argparse.ArgumentParser(
        description="Android Emulator Proxy",
        epilog=proxyctl.USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-a", "--attach", action="store_true", help="Close the emulator when the proxy is stopped")
    parser.add_argument("-s", "--skip", action="store_true", help="Skip emulator launch prompt and start proxy directly")
    parser.add_argument("--clear", action="store_true", help="Clear the proxy settings from a running emulator")
    args = parser.parse_args()

    ATTACH_LIFECYCLE = args.attach

    if args.clear:
        handle_clear_command()

    # Single instance: if a proxy already runs (here or in the background), show it and leave.
    lock = proxyctl.InstanceLock()
    if not lock.acquire(wait=1.0):
        console.print("[yellow]ℹ️  A proxy is already running:[/yellow]")
        proxyctl.print_status(proxyctl.collect_status())
        console.print("\nUse [bold]androidproxy restart[/bold] if it's stale, or [bold]androidproxy stop[/bold] to stop it.")
        sys.exit(0)

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
                while not proxyctl.is_booted(dev):
                    time.sleep(1)
                console.print(f"[green]✅ Boot completed for: {get_device_name(dev)}[/green]")

    console.print(f"[green]✅ Starting microsocks on 127.0.0.1:{SOCKS_PORT} and privoxy on 127.0.0.1:{PRIVOXY_PORT}...[/green]")
    SUPERVISOR = proxyctl.Supervisor(
        build_variant(), lock, mode="foreground", devices=ATTACHED_DEVICES,
        echo=lambda msg: console.print(msg, highlight=False),
    )
    SUPERVISOR.install_signal_handlers()
    SUPERVISOR.start()
    console.print(f"📄 Logging to: {proxyctl.DAEMON_LOG}")
    console.print("🛑 Press Ctrl+C to stop the proxy and restore device settings.")
    try:
        SUPERVISOR.run_forever()
    finally:
        ATTACHED_DEVICES = list(SUPERVISOR.devices)
        SUPERVISOR.shutdown(clear_devices=True)
        if ATTACH_LIFECYCLE:
            kill_emulators()
        console.print("[green]✅ Teardown complete. Goodbye![/green]")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    try:
        main()
    except KeyboardInterrupt:
        # InquirerPy raises KeyboardInterrupt directly (bypassing SIGINT) when Ctrl+C is pressed at a prompt
        cleanup()
