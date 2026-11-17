#!/usr/bin/env python3
import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
import proxyctl  # noqa: E402
from proxyctl import get_device_name, get_running_devices, run_cmd  # noqa: E402

# Configuration
MITMPROXY_PORT = 8080
MITMWEB_PORT = 8081


def mitm_command(executable, filters):
    cmd = [
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
        "--set", "timeout=300",
    ]
    if filters:
        cmd.extend(["--set", f"view_filter={' | '.join(filters)}"])
    return cmd


def parse_filters(args):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-f", "--filters", nargs="*", default=[])
    known, _ = parser.parse_known_args(list(args))
    return known.filters


def build_variant(args=(), open_browser=False):
    """mitmweb owned by the supervisor (background mode and `-w` foreground mode)."""
    filters = parse_filters(args)

    def start_services(logf):
        cmd = mitm_command("mitmweb", filters)
        if not open_browser:
            cmd += ["--set", "web_open_browser=false"]
        return [subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT)]

    return proxyctl.Variant(
        name="mitm",
        command="androidmitm",
        port=MITMPROXY_PORT,
        ports=[MITMPROXY_PORT, MITMWEB_PORT],
        start_services=start_services,
        local_check=lambda: proxyctl.port_open(MITMPROXY_PORT),
        web_url=f"http://127.0.0.1:{MITMWEB_PORT}",
        extra={"filters": filters, "daemon_args": ["-f", *filters] if filters else []},
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
TUI_PROC = None


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

    for pid in LAUNCHED_EMULATOR_PIDS:
        run_cmd(f"kill {pid} 2>/dev/null", capture=False)


def cleanup(signum=None, frame=None):
    """Signal handler: hand teardown to the supervisor once it runs."""
    if SUPERVISOR is not None:
        SUPERVISOR.request_stop()
        if TUI_PROC is not None and TUI_PROC.poll() is None:
            TUI_PROC.terminate()
        return
    print("\n")
    if ATTACH_LIFECYCLE:
        kill_emulators()
    console.print("[green]✅ Nothing to tear down. Goodbye![/green]")
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

    if proxyctl.instance_running():
        console.print("[yellow]ℹ️  The proxy is running and re-applies its setting to attached emulators. "
                      "Use `androidmitm stop` to detach for good.[/yellow]")
    clear_proxy_for_devices(target_devices)
    sys.exit(0)


def main():
    global ATTACH_LIFECYCLE, ATTACHED_DEVICES, LAUNCHED_EMULATOR_PIDS, SUPERVISOR, TUI_PROC

    parser = argparse.ArgumentParser(
        description="Android Emulator mitmproxy",
        epilog=proxyctl.USAGE + "  (androidmitm up -f DOMAIN... runs mitmweb in the background with filters)\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-a", "--attach", action="store_true", help="Close the emulator when the proxy is stopped")
    parser.add_argument("-s", "--skip", action="store_true", help="Skip emulator launch prompt and start proxy directly")
    parser.add_argument("--clear", action="store_true", help="Clear the proxy settings from a running emulator")
    parser.add_argument("-f", "--filters", nargs="*", default=[], help="Filters to apply in mitmproxy (e.g., example.com anotherdomain.com)")
    parser.add_argument("-w", "--web", action="store_true", help="Open mitmproxy in the web browser (mitmweb)")
    args = parser.parse_args()

    ATTACH_LIFECYCLE = args.attach

    if args.clear:
        handle_clear_command()

    # Single instance: if a proxy already runs (here or in the background), show it and leave.
    lock = proxyctl.InstanceLock()
    if not lock.acquire(wait=1.0):
        console.print("[yellow]ℹ️  A proxy is already running:[/yellow]")
        proxyctl.print_status(proxyctl.collect_status())
        console.print("\nUse [bold]androidmitm restart[/bold] if it's stale, or [bold]androidmitm stop[/bold] to stop it.")
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

    echo = lambda msg: console.print(msg, highlight=False)
    if args.web:
        # mitmweb: the supervisor owns it, so restarts and auto-heal work.
        variant = build_variant(["-f", *args.filters] if args.filters else [], open_browser=True)
        SUPERVISOR = proxyctl.Supervisor(variant, lock, mode="foreground", devices=ATTACHED_DEVICES, echo=echo)
        SUPERVISOR.install_signal_handlers()
        console.print("[green]✅ Starting mitmweb...[/green]")
        SUPERVISOR.start()
        console.print("🛑 [bold red]Press Ctrl+C in this terminal to stop and restore device settings.[/bold red]")
        try:
            SUPERVISOR.run_forever()
        finally:
            ATTACHED_DEVICES = list(SUPERVISOR.devices)
            SUPERVISOR.shutdown(clear_devices=True)
    else:
        # Terminal UI: mitmproxy needs this terminal, so the supervisor only watches
        # emulators and health from a thread (it can't restart the TUI for you).
        variant = build_variant()
        SUPERVISOR = proxyctl.Supervisor(variant, lock, mode="foreground", devices=ATTACHED_DEVICES,
                                         owns_services=False)
        for port in variant.ports:
            proxyctl.free_port(port, echo=echo)
        SUPERVISOR.start()
        console.print("[green]✅ Starting mitmproxy...[/green]")
        console.print("🛑 [bold red]Press 'q' then 'y' inside mitmproxy to stop and restore device settings.[/bold red]")
        time.sleep(2)  # brief pause to read the message before mitmproxy takes over
        watcher = threading.Thread(target=SUPERVISOR.run_forever, daemon=True)
        watcher.start()
        try:
            TUI_PROC = subprocess.Popen(mitm_command("mitmproxy", args.filters))
            TUI_PROC.wait()
        except Exception as e:
            console.print(f"[red]Error running mitmproxy: {e}[/red]")
        finally:
            SUPERVISOR.request_stop()
            watcher.join(timeout=10)
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
        cleanup()
