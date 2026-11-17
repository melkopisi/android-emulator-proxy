# Android Emulator Proxy Tools for macOS

This repository contains two lightweight, automated proxy solutions for routing and intercepting Android Emulator traffic on macOS.

## Variants

### 1. [Privoxy / SOCKS5 Proxy (No Interception)](privoxy/README.md)
A lightweight solution for simply routing emulator traffic through host-level VPNs.
- **Tools**: `microsocks`, `privoxy`
- **Use Case**: You just need the Android emulator to access internal company networks via your Mac's VPN, without inspecting the traffic.
- **Pros**: Zero certificate configuration, no HTTPS interception issues.

### 2. [MITM Proxy (Traffic Interception)](mitm/README.md)
An advanced solution for intercepting, viewing, and debugging HTTPS traffic from the emulator.
- **Tools**: `mitmproxy`, `mitmweb`
- **Use Case**: You need to inspect the API calls your app is making, view request/response payloads, or debug network issues.
- **Pros**: Full visibility into HTTPS traffic, web-based dashboard, domain filtering. Requires specific Android Network Security Config.

---

## Single instance, health checks and auto-restart

Both variants share one supervisor (`common/proxyctl.py`). Only one proxy runs at a time, guarded by a lock that macOS releases automatically if the proxy dies, so a crash never leaves a stale lock behind.

| Command (`androidproxy` or `androidmitm`) | What it does |
|---|---|
| `up` | Starts the proxy in the background if it isn't running, then exits. Safe to run as often as you like; a stale proxy gets restarted. `androidmitm up -f api.example.com` takes filters. |
| `status [--json]` | Health, attached emulators, last restart |
| `restart [--hard]` | Restart the proxy processes (`--hard`: kill everything and start again) |
| `stop` | Stop the proxy and clear the emulator proxy setting |
| `logs` | Follow `~/.android-proxy-logs/daemon.log` |
| `config` | `check-url URL`, `auto-heal on/off`, `network-restart on/off`, `health-interval SECONDS` |

Running the old interactive way (`androidproxy`, `androidmitm -w`) still works. If a proxy is already running it shows its status and exits instead of starting a second copy.

### How "stale" is detected

Every 10 seconds the proxy fetches a check URL two ways: **directly from the Mac** and **through the proxy**.

| Direct | Through proxy | Menu bar | What happens |
|---|---|---|---|
| ✅ | ✅ | 🟢 | nothing |
| ✅ | ❌ | 🔴 stale | auto-restart after 2 failed checks (restart fixes it) |
| ❌ | ❌ | 🟠 VPN | notification only: your VPN is down, restarting won't help |
| – | proxy port dead | 🔴 down | auto-restart |

The installer asks for this check URL; press Enter to keep the default (`https://www.google.com/generate_204`). With the default, the check catches a stale proxy but can't tell when your VPN is down. For that, **use a host behind your VPN**, such as your staging API (any HTTP answer counts, even 401/404). To change it later, run this or use **Settings → Set check URL…** in the menu bar:

```bash
androidproxy config check-url https://staging.internal.example.com/health
```

It also:
- restarts the proxy when the network changes (a VPN tunnel interface appears or disappears, the default route moves, or the Mac wakes from sleep), before your next API call hangs on a dead connection
- restarts microsocks/privoxy/mitmweb if one of them exits
- attaches every emulator as it boots, and re-applies the proxy setting if an emulator loses it
- privoxy no longer keeps upstream connections alive for long (`keep-alive-timeout 5`, `connection-sharing 0`), so pooled connections can't go stale when the tunnel reconnects

### Menu bar status (SwiftBar)

Both installers offer this at the end. Answer **y** and they install [SwiftBar](https://swiftbar.app) (via Homebrew) if you don't have it and add the plugin; answer **n** (the default) and nothing menu-bar related is touched. To add it later, or skip the question:

```bash
./swiftbar/install.sh          # asks
./swiftbar/install.sh --yes    # installs without asking
```

The icon refreshes every 5 seconds: 🟢 healthy · 🟡 slow/starting · 🔴 stale (restart fixes it) · 🟠 VPN down · ⚪ not running. The menu shows direct vs. proxied latency, attached emulators and the last restart reason, plus **Restart** (hold ⌥ for **Hard restart**), **Stop**/**Start**, **Open mitmweb**, the log, and toggles for auto-restart and the check URL.

### Android Studio

Make the proxy start whenever you run the app, and keep a restart button one shortcut away:

1. **Settings → Tools → External Tools → +**, create two tools:

   | Name | Program | Arguments |
   |---|---|---|
   | `Proxy: Up` | `$USER_HOME$/.local/bin/androidproxy` | `up` |
   | `Proxy: Restart` | `$USER_HOME$/.local/bin/androidproxy` | `restart` |

   (Use `androidmitm` for the interception variant.) Both finish in a second, so the console tab doesn't pile up.
2. **Run → Edit Configurations → your app → Before launch → + → Run External tool → `Proxy: Up`**. Every Run/Debug now makes sure the proxy is up; when it's already healthy this takes about 100 ms.
3. **Settings → Keymap → External Tools → `Proxy: Restart`** → add a shortcut, e.g. `⌃⌥P`.

---

## License

[Apache License V2](LICENSE)
