"""Single-instance supervisor, health checks and control commands shared by
both proxy variants (privoxy and mitm).

Only one proxy runs at a time (both variants set the same emulator setting).
That is enforced with an flock on ~/.android-proxy/proxy.lock: the kernel drops
the lock when the owning process dies, so a crash never leaves a stale lock.

The running proxy (foreground or background) writes ~/.android-proxy/state.json
every few seconds. `status` and the SwiftBar menu only read that file, so they
are fast and never touch adb or the network themselves.
"""
from __future__ import annotations

import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

STATE_DIR = Path.home() / ".android-proxy"
LOCK_FILE = STATE_DIR / "proxy.lock"
STATE_FILE = STATE_DIR / "state.json"
CONFIG_FILE = STATE_DIR / "config.json"
LOG_DIR = Path.home() / ".android-proxy-logs"
DAEMON_LOG = LOG_DIR / "daemon.log"
BIN_DIR = Path.home() / ".local" / "bin"

EMULATOR_HOST_IP = "10.0.2.2"
TICK_SECONDS = 3
DRIFT_CHECK_EVERY_TICKS = 5
HEARTBEAT_STALE_AFTER = 30
HEAL_AFTER_FAILURES = 2
HEAL_COOLDOWN = 30
NETWORK_SETTLE_SECONDS = 3
NETWORK_RESTART_THROTTLE = 10
WAKE_JUMP_SECONDS = 20
LOG_MAX_BYTES = 5 * 1024 * 1024

DEFAULT_CONFIG = {
    # Any HTTP answer (even 401/404) counts as reachable. Point this at a host
    # behind your VPN so the check can tell "proxy stale" from "VPN down".
    # HTTPS on purpose: it exercises the CONNECT tunnel your app's traffic uses.
    "probe_url": "https://www.google.com/generate_204",
    "auto_heal": True,
    "restart_on_network_change": True,
    "health_interval": 10,
    "slow_threshold": 2.0,
}

KNOWN_VARIANTS = {
    "privoxy": {"command": "androidproxy", "label": "VPN routing (privoxy)"},
    "mitm": {"command": "androidmitm", "label": "Interception (mitmweb)"},
}

SUBCOMMANDS = {"up", "status", "restart", "stop", "logs", "config", "swiftbar", "_daemon"}

# state -> (icon, short title text, does a proxy restart fix it?)
STATES = {
    "healthy": ("🟢", "", False),
    "slow": ("🟡", "slow", False),
    "starting": ("🟡", "starting", False),
    "proxy_down": ("🔴", "down", True),
    "proxy_stale": ("🔴", "stale", True),
    "upstream_down": ("🟠", "VPN", False),
    "unresponsive": ("🔴", "hung", True),
    "not_running": ("⚪", "", False),
}
BAD_STATES = {"proxy_down", "proxy_stale", "upstream_down", "unresponsive"}
# Status codes the proxy itself answers with when forwarding fails
# (privoxy: "500 Internal Privoxy Error" when microsocks is gone, 502/503/504 otherwise).
GATEWAY_ERRORS = {"500", "502", "503", "504"}


# --------------------------------------------------------------------------
# Shell / adb helpers
# --------------------------------------------------------------------------

def run_cmd(cmd, capture=True, timeout=None):
    """Run a shell command and return stdout. Errors and timeouts return ""."""
    try:
        res = subprocess.run(cmd, shell=True, capture_output=capture, text=True, timeout=timeout)
        if capture:
            return res.stdout.strip()
    except Exception:
        pass
    return ""


def get_running_devices():
    """Return adb serials of running emulators (physical devices are ignored)."""
    out = run_cmd("adb devices", timeout=10)
    devices = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device" and parts[0].startswith("emulator-"):
            devices.append(parts[0])
    return devices


def get_device_name(serial):
    """Human-readable name for an emulator: AVD name, else model, else serial."""
    name_out = run_cmd(f"adb -s {serial} emu avd name 2>/dev/null", timeout=10)
    if name_out:
        first_line = name_out.splitlines()[0].strip()
        if first_line and first_line != "OK" and "error:" not in first_line:
            return f"{first_line} ({serial})"
    model = run_cmd(f"adb -s {serial} shell getprop ro.product.model 2>/dev/null", timeout=10)
    if model:
        return f"{model} ({serial})"
    return serial


def is_booted(serial):
    return run_cmd(f"adb -s {serial} shell getprop sys.boot_completed 2>/dev/null", timeout=10) == "1"


def set_device_proxy(serial, port):
    run_cmd(f"adb -s {serial} shell settings put global http_proxy {EMULATOR_HOST_IP}:{port}", timeout=10)


def get_device_proxy(serial):
    value = run_cmd(f"adb -s {serial} shell settings get global http_proxy 2>/dev/null", timeout=10)
    return "" if value == "null" else value


def clear_device_proxy(serial):
    for key in ("http_proxy", "global_http_proxy_host", "global_http_proxy_port"):
        run_cmd(f"adb -s {serial} shell settings delete global {key} 2>/dev/null", timeout=10)


def free_port(port, echo=None):
    """Kill any process listening on the given port."""
    pids = run_cmd(f"lsof -Pi :{port} -sTCP:LISTEN -t 2>/dev/null", timeout=10)
    if pids:
        pids = pids.replace("\n", " ")
        if echo:
            echo(f"🧹 Freeing port {port} (killing PIDs: {pids})")
        run_cmd(f"kill -9 {pids} 2>/dev/null", capture=False, timeout=10)
        time.sleep(0.5)


def port_open(port, host="127.0.0.1", timeout=1.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def socks5_handshake_ok(port, host="127.0.0.1", timeout=1.0):
    """True if a SOCKS5 server answers the no-auth greeting."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(b"\x05\x01\x00")
            return s.recv(2) == b"\x05\x00"
    except OSError:
        return False


def notify(title, message):
    """Show a macOS notification (fire and forget)."""
    script = (f"display notification {json.dumps(message, ensure_ascii=False)} "
              f"with title {json.dumps(title, ensure_ascii=False)}")
    try:
        subprocess.Popen(["osascript", "-e", script],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def network_fingerprint():
    """Changes whenever an interface (e.g. a VPN utun) appears/disappears or the default route moves."""
    ifaces = run_cmd("ifconfig -l", timeout=5)
    route = run_cmd("route -n get default 2>/dev/null | awk '/interface:/{print $2}'", timeout=5)
    return f"{ifaces}|{route}"


# --------------------------------------------------------------------------
# Config & state files
# --------------------------------------------------------------------------

def _atomic_write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.replace(tmp, path)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text()))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    _atomic_write_json(CONFIG_FILE, cfg)


def read_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


# --------------------------------------------------------------------------
# Single-instance lock
# --------------------------------------------------------------------------

class InstanceLock:
    """Exclusive flock on LOCK_FILE. Released automatically if the process dies."""

    def __init__(self):
        self._fd = None

    def acquire(self, wait=0.0):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fd = fd
                return True
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    return False
                time.sleep(0.1)

    def release(self):
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


def instance_running():
    probe = InstanceLock()
    if probe.acquire():
        probe.release()
        return False
    return True


# --------------------------------------------------------------------------
# Health checks
# --------------------------------------------------------------------------

@dataclass
class ProbeResult:
    ok: bool
    code: str = "000"
    seconds: Optional[float] = None

    def to_dict(self):
        return {"ok": self.ok, "code": self.code, "seconds": self.seconds}


def curl_probe(url, proxy=None, timeout=4.0):
    """Fetch url (directly, or through proxy). Any HTTP status counts as reachable."""
    cmd = ["curl", "-sk", "-o", "/dev/null", "-w", "%{http_code} %{time_total}",
           "--max-time", str(timeout)]
    cmd += ["-x", proxy] if proxy else ["--noproxy", "*"]
    cmd.append(url)
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2).stdout.strip()
    except Exception:
        return ProbeResult(False)
    code, _, secs = out.partition(" ")
    code = code or "000"
    try:
        seconds = round(float(secs), 3)
    except ValueError:
        seconds = None
    ok = code != "000"
    return ProbeResult(ok, code, seconds if ok else None)


def verdict(local_ok, direct, proxied, slow_threshold=2.0):
    """Classify one health check. Pure function: easy to test.

    local_ok: the proxy's own ports answer.
    direct:   the probe URL fetched from the Mac without the proxy.
    proxied:  the same URL fetched through the proxy (what the emulator does).
    """
    if not local_ok:
        return {"state": "proxy_down", "summary": "Proxy port is not answering"}
    # A 5xx gateway error that the target itself didn't return is the proxy's
    # own error page ("forwarding failure"), i.e. the request didn't get through.
    proxied_ok = proxied.ok and not (proxied.code in GATEWAY_ERRORS and proxied.code != direct.code)
    if direct.ok and not proxied_ok:
        return {"state": "proxy_stale",
                "summary": "Reachable from the Mac but not through the proxy"}
    if not direct.ok and not proxied_ok:
        return {"state": "upstream_down",
                "summary": "Unreachable from the Mac too: VPN down?"}
    if proxied.seconds is not None and proxied.seconds > slow_threshold:
        return {"state": "slow", "summary": f"Slow through the proxy ({proxied.seconds:.1f}s)"}
    return {"state": "healthy", "summary": "Healthy"}


def fmt_age(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    return f"{hours // 24}d {hours % 24}h"


def fmt_ms(seconds):
    return "—" if seconds is None else f"{int(seconds * 1000)}ms"


# --------------------------------------------------------------------------
# Supervisor: owns the proxy processes, emulators, health and the state file
# --------------------------------------------------------------------------

@dataclass
class Variant:
    name: str                                   # "privoxy" | "mitm"
    command: str                                # wrapper in ~/.local/bin
    port: int                                   # port the emulator connects to
    ports: list                                 # every port to free before (re)starting
    start_services: Callable                    # (logfile) -> [Popen]
    local_check: Callable                       # () -> bool
    web_url: Optional[str] = None
    extra: dict = field(default_factory=dict)


def _open_log():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if DAEMON_LOG.stat().st_size > LOG_MAX_BYTES:
            os.replace(DAEMON_LOG, DAEMON_LOG.with_suffix(".log.1"))
    except OSError:
        pass
    return open(DAEMON_LOG, "a", buffering=1)


class Supervisor:
    def __init__(self, variant, lock, *, mode, devices=(), echo=None, owns_services=True):
        self.v = variant
        self.lock = lock
        self.mode = mode                    # "background" | "foreground"
        self.echo = echo
        self.owns_services = owns_services
        self.config = load_config()
        self.procs = []
        self.devices = {}
        self.health = {"state": "starting", "summary": "Waiting for the first check", "checked_at": None}
        self.started_at = time.time()
        self.last_restart = None
        self.restart_count = 0
        self.logf = _open_log()
        self._mutex = threading.RLock()
        self._wake = threading.Event()
        self._stop_requested = False
        self._restart_requested = False
        self._shut_down = False
        self._fail_streak = 0
        self._last_heal = 0.0
        self._last_net_restart = 0.0
        self._net_fp = network_fingerprint()
        self._net_change_at = None
        self._net_change_reason = ""
        self._next_health = 0.0
        self._tick = 0
        for serial in devices:
            self.devices[serial] = {"name": get_device_name(serial), "ok": True}

    # -- logging ------------------------------------------------------------
    def log(self, msg):
        self.logf.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{self.v.name}] {msg}\n")
        if self.echo:
            self.echo(msg)

    # -- signals (safe to call from a signal handler) -----------------------
    def request_stop(self, *_):
        self._stop_requested = True
        self._wake.set()

    def request_restart(self, *_):
        self._restart_requested = True
        self._wake.set()

    def install_signal_handlers(self):
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGHUP, self.request_stop)
        signal.signal(signal.SIGUSR1, self.request_restart)

    # -- services -----------------------------------------------------------
    def _start_services(self):
        for port in self.v.ports:
            free_port(port, echo=self.log)
        self.procs = self.v.start_services(self.logf)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.v.local_check():
                return True
            if any(p.poll() is not None for p in self.procs):
                break
            time.sleep(0.2)
        self.log("⚠️  Proxy did not come up cleanly; see the log")
        return False

    def _stop_services(self):
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
        for p in self.procs:
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
        self.procs = []

    def restart_services(self, reason):
        if not self.owns_services:
            self.log(f"↻ Restart requested ({reason}) but this session's proxy runs in the foreground; restart it there")
            return
        with self._mutex:
            self.log(f"↻ Restarting proxy: {reason}")
            self._stop_services()
            self._start_services()
            self.restart_count += 1
            self.last_restart = {"at": time.time(), "reason": reason}
            self._fail_streak = 0
            self._next_health = 0.0
            self.log("✅ Proxy restarted")

    # -- emulators ----------------------------------------------------------
    def _attach(self, serial):
        set_device_proxy(serial, self.v.port)
        name = get_device_name(serial)
        self.devices[serial] = {"name": name, "ok": True}
        self.log(f"🎉 Proxy attached to {name} ({EMULATOR_HOST_IP}:{self.v.port})")

    def _devices_tick(self):
        running = set(get_running_devices())
        for serial in list(self.devices):
            if serial not in running:
                self.log(f"📴 {self.devices[serial]['name']} went away")
                del self.devices[serial]
        for serial in sorted(running - set(self.devices)):
            if is_booted(serial):
                self._attach(serial)
        if self._tick % DRIFT_CHECK_EVERY_TICKS == 0:
            expected = f"{EMULATOR_HOST_IP}:{self.v.port}"
            for serial, info in self.devices.items():
                current = get_device_proxy(serial)
                if not is_booted(serial):
                    continue  # adb can't answer reliably mid-boot; don't misread that as drift
                if current and current != expected:
                    self.log(f"⚠️  {info['name']} proxy was '{current}', re-applying {expected}")
                    set_device_proxy(serial, self.v.port)
                    info["reapplied_at"] = time.time()
                elif not current:
                    self.log(f"⚠️  {info['name']} lost its proxy setting, re-applying")
                    set_device_proxy(serial, self.v.port)
                    info["reapplied_at"] = time.time()

    # -- network / sleep ----------------------------------------------------
    def _network_tick(self, wall_jump):
        if wall_jump > WAKE_JUMP_SECONDS:
            self._net_change_at = time.monotonic()
            self._net_change_reason = "Mac woke from sleep"
        fp = network_fingerprint()
        if fp != self._net_fp:
            self._net_fp = fp
            self._net_change_at = time.monotonic()
            self._net_change_reason = "network changed (VPN tunnel or Wi-Fi)"
        if self._net_change_at is None:
            return
        if time.monotonic() - self._net_change_at < NETWORK_SETTLE_SECONDS:
            return  # let the tunnel settle before acting
        reason = self._net_change_reason
        self._net_change_at = None
        self._next_health = 0.0
        if (self.config.get("restart_on_network_change")
                and time.monotonic() - self._last_net_restart > NETWORK_RESTART_THROTTLE):
            self._last_net_restart = time.monotonic()
            self.restart_services(reason)
        else:
            self.log(f"🌐 {reason}")

    # -- health -------------------------------------------------------------
    def check_health(self):
        self.config = load_config()
        url = self.config["probe_url"]
        results = {}

        def probe(key, proxy):
            results[key] = curl_probe(url, proxy)

        threads = [threading.Thread(target=probe, args=("direct", None)),
                   threading.Thread(target=probe, args=("proxied", f"http://127.0.0.1:{self.v.port}"))]
        for t in threads:
            t.start()
        local_ok = self.v.local_check()
        for t in threads:
            t.join()

        v = verdict(local_ok, results["direct"], results["proxied"], self.config["slow_threshold"])
        prev = self.health.get("state")
        self.health = {**v, "checked_at": time.time(), "probe_url": url, "local_ok": local_ok,
                       "direct": results["direct"].to_dict(), "proxied": results["proxied"].to_dict()}
        state = v["state"]
        if state != prev:
            self.log(f"{STATES[state][0]} {v['summary']}")
            if state == "upstream_down":
                notify("Android proxy: VPN down",
                       "The target isn't reachable from the Mac either. Restarting the proxy won't help.")
            elif prev in BAD_STATES and state in ("healthy", "slow"):
                notify("Android proxy", "Back to healthy ✅")

        if not STATES[state][2]:
            self._fail_streak = 0
            return
        self._fail_streak += 1
        if self._fail_streak < HEAL_AFTER_FAILURES:
            return
        if self.config.get("auto_heal") and self.owns_services:
            if time.time() - self._last_heal > HEAL_COOLDOWN:
                self._last_heal = time.time()
                notify("Android proxy was stale", "Restarted it automatically.")
                self.restart_services(f"auto-heal ({v['summary']})")
        elif self._fail_streak == HEAL_AFTER_FAILURES:
            notify("Android proxy is stale", "Restart it from the menu bar.")

    # -- state file ---------------------------------------------------------
    def write_state(self):
        _atomic_write_json(STATE_FILE, {
            "pid": os.getpid(),
            "variant": self.v.name,
            "command": self.v.command,
            "mode": self.mode,
            "owns_services": self.owns_services,
            "port": self.v.port,
            "web_url": self.v.web_url,
            "extra": self.v.extra,
            "started_at": self.started_at,
            "heartbeat": time.time(),
            "health": self.health,
            "devices": [{"serial": s, **info} for s, info in sorted(self.devices.items())],
            "last_restart": self.last_restart,
            "restart_count": self.restart_count,
            "log": str(DAEMON_LOG),
        })

    # -- lifecycle ----------------------------------------------------------
    def start(self):
        self.log(f"🚀 Starting ({self.mode}, pid {os.getpid()})")
        if self.owns_services:
            self._start_services()
        for serial in list(self.devices):
            self._attach(serial)
        self.write_state()

    def run_forever(self):
        last_wall, last_mono = time.time(), time.monotonic()
        while not self._stop_requested:
            try:
                now_wall, now_mono = time.time(), time.monotonic()
                wall_jump = (now_wall - last_wall) - (now_mono - last_mono)
                last_wall, last_mono = now_wall, now_mono

                if self._restart_requested:
                    self._restart_requested = False
                    self.restart_services("manual restart")
                if self.owns_services:
                    dead = [p for p in self.procs if p.poll() is not None]
                    if dead:
                        self.restart_services(f"{Path(str(dead[0].args[0])).name} exited")
                self._network_tick(wall_jump)
                self._devices_tick()
                if time.monotonic() >= self._next_health:
                    # Schedule first: a restart inside check_health() resets this
                    # to 0 so the result is re-checked right away.
                    self._next_health = time.monotonic() + float(self.config["health_interval"])
                    self.check_health()
                self.write_state()
            except Exception as exc:  # never let the supervisor die on a hiccup
                self.log(f"⚠️  Supervisor error: {exc!r}")
            self._tick += 1
            self._wake.wait(TICK_SECONDS)
            self._wake.clear()

    def shutdown(self, clear_devices=True):
        if self._shut_down:
            return
        self._shut_down = True
        self.log("🛑 Stopping proxy")
        if self.owns_services:
            self._stop_services()
            for port in self.v.ports:
                free_port(port)
        if clear_devices:
            for serial, info in self.devices.items():
                self.log(f"🧹 Clearing proxy settings from {info['name']}")
                clear_device_proxy(serial)
        try:
            STATE_FILE.unlink()
        except OSError:
            pass
        self.log("✅ Teardown complete")
        self.lock.release()


# --------------------------------------------------------------------------
# Status (shared by the CLI and the menu bar)
# --------------------------------------------------------------------------

def collect_status():
    if not instance_running():
        return {"running": False, "state": "not_running", "summary": "Not running"}
    st = read_state()
    if not st:
        return {"running": True, "state": "starting", "summary": "Starting…"}
    health = st.get("health") or {}
    age = time.time() - st.get("heartbeat", 0)
    if age > HEARTBEAT_STALE_AFTER:
        state, summary = "unresponsive", f"Supervisor hasn't reported for {fmt_age(age)}"
    else:
        state, summary = health.get("state", "starting"), health.get("summary", "")
    return {**st, "running": True, "state": state, "summary": summary}


def print_status(info):
    icon = STATES[info["state"]][0]
    if not info["running"]:
        print(f"{icon} Not running")
        return
    now = time.time()
    h = info.get("health") or {}
    print(f"{icon} {info['summary']}")
    print(f"   {info['variant']} · {info['mode']} · pid {info['pid']} · up {fmt_age(now - info['started_at'])}"
          f" · emulators use {EMULATOR_HOST_IP}:{info['port']}")
    if h.get("checked_at"):
        print(f"   Probe {h.get('probe_url')}: direct {fmt_ms(h['direct']['seconds'])} ({h['direct']['code']}),"
              f" via proxy {fmt_ms(h['proxied']['seconds'])} ({h['proxied']['code']}),"
              f" checked {fmt_age(now - h['checked_at'])} ago")
    if info.get("last_restart"):
        lr = info["last_restart"]
        print(f"   Last restart {fmt_age(now - lr['at'])} ago: {lr['reason']}")
    for d in info.get("devices", []):
        print(f"   📱 {d['name']}")
    if not info.get("devices"):
        print("   📱 No emulators attached yet (they attach automatically when they boot)")
    if info.get("web_url"):
        print(f"   🌐 {info['web_url']}")


# --------------------------------------------------------------------------
# SwiftBar menu
# --------------------------------------------------------------------------

def _sb_action(label, command, *params, refresh=True, extra=""):
    parts = [f'bash="{command}"']
    parts += [f'param{i}="{p}"' for i, p in enumerate(params, 1)]
    parts.append("terminal=false")
    if refresh:
        parts.append("refresh=true")
    return f"{label} | {' '.join(parts)} {extra}".rstrip()


def render_swiftbar(info, config):
    now = time.time()
    icon, short, _ = STATES[info["state"]]
    lines = [f"{icon} {short}".strip(), "---"]
    installed = {name: BIN_DIR / meta["command"] for name, meta in KNOWN_VARIANTS.items()
                 if (BIN_DIR / meta["command"]).exists()}
    any_cmd = str(next(iter(installed.values()), BIN_DIR / "androidproxy"))

    if not info["running"]:
        lines.append("Android proxy is not running | color=gray")
        lines.append("---")
        for name, path in installed.items():
            lines.append(_sb_action(f"▶ Start {KNOWN_VARIANTS[name]['label']}", str(path), "up"))
    else:
        cmd = str(BIN_DIR / info["command"])
        h = info.get("health") or {}
        color = "red" if STATES[info["state"]][2] else "green" if info["state"] == "healthy" else "orange"
        lines.append(f"{info['summary']} | color={color}")
        lines.append(f"{KNOWN_VARIANTS.get(info['variant'], {}).get('label', info['variant'])} · "
                     f"{info['mode']} · up {fmt_age(now - info['started_at'])} | size=12")
        if h.get("checked_at"):
            lines.append(f"Direct {fmt_ms(h['direct']['seconds'])} ({h['direct']['code']}) · "
                         f"via proxy {fmt_ms(h['proxied']['seconds'])} ({h['proxied']['code']}) | size=12 font=Menlo")
            lines.append(f"Checked {fmt_age(now - h['checked_at'])} ago | size=12")
        if info.get("last_restart"):
            lr = info["last_restart"]
            lines.append(f"Restarted {fmt_age(now - lr['at'])} ago: {lr['reason']} | size=12")
        lines.append("---")
        lines.append("Emulators")
        for d in info.get("devices", []):
            lines.append(f"📱 {d['name']} → {EMULATOR_HOST_IP}:{info['port']}")
        if not info.get("devices"):
            lines.append("No emulators attached yet | color=gray")
        lines.append("---")
        if info["state"] == "upstream_down":
            lines.append("Your VPN looks down: restarting the proxy won't fix it | color=orange size=12")
        lines.append(_sb_action("↻ Restart proxy", cmd, "restart"))
        lines.append(_sb_action("↻ Hard restart (kill everything)", cmd, "restart", "--hard", extra="alternate=true"))
        lines.append(_sb_action("■ Stop proxy", cmd, "stop"))
        if info.get("web_url"):
            lines.append(f"🌐 Open mitmweb | href={info['web_url']}")
    lines.append("---")
    lines.append(_sb_action("📄 Open log", "/usr/bin/open", "-a", "Console", str(DAEMON_LOG), refresh=False))
    lines.append("Settings")
    on_off = lambda b: "On ✓" if b else "Off"
    lines.append("--" + _sb_action(f"Auto-restart when stale: {on_off(config['auto_heal'])}", any_cmd,
                                   "config", "auto-heal", "off" if config["auto_heal"] else "on"))
    lines.append("--" + _sb_action(f"Restart on network change: {on_off(config['restart_on_network_change'])}", any_cmd,
                                   "config", "network-restart", "off" if config["restart_on_network_change"] else "on"))
    lines.append(f"--Check URL: {config['probe_url']} | size=12")
    lines.append("--" + _sb_action("Set check URL (a host behind your VPN)…", any_cmd, "config", "check-url", "--prompt"))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _wait_for(predicate, timeout, interval=0.25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def run_daemon(variant):
    """Entry point of the detached background process."""
    lock = InstanceLock()
    if not lock.acquire(wait=2.0):
        return 0  # someone else already owns the proxy
    sup = Supervisor(variant, lock, mode="background",
                     devices=[s for s in get_running_devices() if is_booted(s)])
    sup.install_signal_handlers()
    try:
        sup.start()
        sup.run_forever()
    finally:
        sup.shutdown(clear_devices=True)
    return 0


def cmd_up(variant, script_path, daemon_args):
    if instance_running():
        info = collect_status()
        if info.get("variant") and info["variant"] != variant.name:
            print(f"⚠️  The {info['variant']} proxy is already running. Stop it first: {info['command']} stop")
            return 1
        if STATES[info["state"]][2]:
            print(f"{STATES[info['state']][0]} {info['summary']}: restarting")
            return cmd_restart(variant, script_path, hard=info["state"] == "unresponsive")
        print_status(info)
        print("✅ Already running, nothing to do.")
        return 0

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(DAEMON_LOG, "a") as logf:
        subprocess.Popen([sys.executable, str(script_path), "_daemon", *daemon_args],
                         stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
                         start_new_session=True, close_fds=True)
    print(f"🚀 Starting {variant.name} proxy in the background…")
    if not _wait_for(lambda: collect_status()["state"] not in ("not_running", "starting"), timeout=20):
        print(f"⚠️  Still starting. Check `{variant.command} status` or `{variant.command} logs`.")
        return 1
    print_status(collect_status())
    return 0


def cmd_stop(variant, quiet=False):
    if not instance_running():
        for port in variant.ports:
            free_port(port)  # leftovers from a crashed run
        if not quiet:
            print("⚪ Not running")
        return 0
    st = read_state()
    pid = st.get("pid")
    if not pid:
        print("⚠️  Proxy is starting; try again in a moment")
        return 1
    print(f"🛑 Stopping {st.get('variant')} proxy (pid {pid})…")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    if not _wait_for(lambda: not instance_running(), timeout=15):
        print("⚠️  Not responding, killing it")
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        _wait_for(lambda: not instance_running(), timeout=3)
        for d in st.get("devices", []):
            clear_device_proxy(d["serial"])
        try:
            STATE_FILE.unlink()
        except OSError:
            pass
    for port in variant.ports:
        free_port(port)
    print("✅ Stopped")
    return 0


def cmd_restart(variant, script_path, hard=False):
    if not instance_running():
        return cmd_up(variant, script_path, [])
    info = collect_status()
    if info.get("variant") and info["variant"] != variant.name:
        print(f"⚠️  The {info['variant']} proxy is running, not {variant.name}. Use: {info['command']} restart")
        return 1
    if hard or info["state"] == "unresponsive" or not info.get("owns_services"):
        if not info.get("owns_services") and not hard:
            print("ℹ️  That proxy runs in a foreground terminal; moving it to the background.")
        daemon_args = (info.get("extra") or {}).get("daemon_args", [])
        cmd_stop(variant)
        return cmd_up(variant, script_path, daemon_args)

    before = info.get("restart_count", 0)
    print(f"↻ Restarting {variant.name} proxy…")
    os.kill(info["pid"], signal.SIGUSR1)
    if not _wait_for(lambda: read_state().get("restart_count", 0) > before, timeout=15):
        print("⚠️  Soft restart didn't happen, doing a hard restart")
        return cmd_restart(variant, script_path, hard=True)
    restarted_at = (read_state().get("last_restart") or {}).get("at", 0)
    _wait_for(lambda: ((read_state().get("health") or {}).get("checked_at") or 0) > restarted_at, timeout=12)
    print_status(collect_status())
    return 0


def cmd_config(args):
    cfg = load_config()
    if not args:
        print(json.dumps(cfg, indent=2))
        print(f"\n(file: {CONFIG_FILE})")
        return 0
    key, rest = args[0], args[1:]
    toggles = {"auto-heal": "auto_heal", "network-restart": "restart_on_network_change"}
    if key in toggles and rest and rest[0] in ("on", "off"):
        cfg[toggles[key]] = rest[0] == "on"
    elif key in ("check-url", "probe-url") and rest:
        value = rest[0]
        if value == "--ask":
            return _ask_check_url_in_terminal(cfg)
        if value == "--prompt":
            script = ('text returned of (display dialog "URL to check through the proxy, ideally a host behind your VPN '
                      '(any HTTP answer counts, even 401/404):" '
                      f'default answer {json.dumps(cfg["probe_url"])} with title "Android Proxy")')
            res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            if res.returncode != 0:
                return 0  # cancelled
            value = res.stdout.strip()
        elif value == "--reset":
            value = DEFAULT_CONFIG["probe_url"]
        if not value.startswith(("http://", "https://")):
            print("⚠️  The check URL must start with http:// or https://")
            return 1
        cfg["probe_url"] = value
    elif key == "health-interval" and rest and rest[0].isdigit():
        cfg["health_interval"] = max(3, int(rest[0]))
    else:
        print("Usage: config [auto-heal on|off] [network-restart on|off] "
              "[check-url URL|--ask|--prompt|--reset] [health-interval SECONDS]")
        return 1
    save_config(cfg)
    print(f"✅ Saved. The running proxy picks it up at its next check.")
    return 0


def _ask_check_url_in_terminal(cfg):
    """Installer question: which URL to health-check. Enter keeps the current one."""
    # Separate handles: a terminal isn't seekable, so open("/dev/tty", "r+") fails.
    try:
        tty_in = open("/dev/tty")
        tty_out = open("/dev/tty", "w")
    except OSError:
        print(f"ℹ️  Health-check URL: {cfg['probe_url']} "
              "(change it later with: config check-url URL)")
        return 0
    current = cfg["probe_url"]
    with tty_in, tty_out:
        tty_out.write("\n🔎 Health-check URL\n"
                      "   The proxy fetches this URL every few seconds, both directly and through the proxy,\n"
                      "   to tell a stale proxy (restart fixes it) from a VPN that's down.\n"
                      "   Use a host behind your VPN (e.g. your staging API) to detect VPN outages;\n"
                      "   any HTTP answer counts, even 401/404. Press Enter to keep the default.\n")
        for _ in range(3):
            tty_out.write(f"   URL [{current}]: ")
            tty_out.flush()
            value = tty_in.readline().strip() or current
            if value.startswith(("http://", "https://")):
                break
            tty_out.write("   ⚠️  It must start with http:// or https://\n")
            tty_out.flush()
        else:
            value = current
    cfg["probe_url"] = value
    save_config(cfg)
    print(f"✅ Health-check URL: {value}")
    return 0


USAGE = """Commands:
  up                 Start the proxy in the background if it isn't running (safe to run repeatedly)
  status [--json]    Show health, attached emulators and the last restart
  restart [--hard]   Restart the proxy processes (--hard: kill and start again)
  stop               Stop the proxy and clear emulator proxy settings
  logs               Follow the proxy log
  config ...         Show or change settings (check URL, auto-heal, ...)
"""


def cli(build_variant, script_path, argv):
    """Handle a control subcommand. build_variant(args) -> Variant."""
    cmd, args = argv[0], argv[1:]
    if cmd == "_daemon":
        return run_daemon(build_variant(args))
    if cmd == "status":
        info = collect_status()
        if "--json" in args:
            print(json.dumps(info, indent=2, ensure_ascii=False))
        else:
            print_status(info)
        return 0 if info["state"] not in BAD_STATES else 2
    if cmd == "swiftbar":
        print(render_swiftbar(collect_status(), load_config()))
        return 0
    if cmd == "logs":
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        DAEMON_LOG.touch()
        os.execvp("tail", ["tail", "-n", "50", "-F", str(DAEMON_LOG)])
    if cmd == "config":
        return cmd_config(args)
    variant = build_variant(args)
    if cmd == "up":
        return cmd_up(variant, script_path, args)
    if cmd == "stop":
        return cmd_stop(variant)
    if cmd == "restart":
        return cmd_restart(variant, script_path, hard="--hard" in args)
    print(USAGE)
    return 1
