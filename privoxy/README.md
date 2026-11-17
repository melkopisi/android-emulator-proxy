# Android Emulator VPN Routing Proxy (No Interception)

A lightweight and automated proxy solution for routing Android Emulator traffic through host-level VPNs on macOS — without requiring certificate installation or HTTPS interception.

---

## ⚙️ Problem Overview

On macOS, Android Emulator runs in a virtualized environment that **does not automatically inherit host-level network settings**, such as VPN routes or proxy configurations. This causes major issues when:

- You are running a VPN on macOS that is required to access internal development/staging environments.
- An Android app running inside the emulator needs to reach those environments.
- The VPN is enforced via a SOCKS5 or tunnel that only the host can access.

**Result:** The emulator cannot connect to required services, even though your Mac can.

---

## ✅ Solution

This tool sets up a **local proxy chain** on your Mac to route emulator traffic **through your host's VPN connection**, using only built-in emulator support:

- **microsocks** – creates a local SOCKS5 proxy on `127.0.0.1:1080`
- **Privoxy** – converts emulator-friendly HTTP proxy requests to SOCKS5
- **ADB Configuration** – automatically sets the emulator's internal proxy to forward all HTTP/S traffic through this bridge (`10.0.2.2:8118`)

By using the special Android Emulator IP `10.0.2.2`, which maps to the host machine, we ensure traffic is routed via the VPN — **with zero certificate configuration** and **no HTTPS interception**.

---

## 📦 Features

- 🔄 Routes emulator traffic through host VPN (e.g. WireGuard, OpenVPN, Cisco, etc.)
- 🧠 Automatically sets the emulator’s global HTTP proxy via `adb`
- 🎯 **Fuzzy Multi-Select UI**: Beautiful, interactive prompt using `InquirerPy` to fuzzy-search and select multiple running emulators.
- 📜 Isolated Python Environment Installer: Installs dependencies safely via a lightweight `.venv` wrapper to bypass macOS `pip` restrictions.
- ⚙️ Auto-kills previous proxy instances to avoid port conflicts and efficiently handles ports 1080 and 8118.
- 🛡️ Lifecycle Isolation: Closing the proxy (`Ctrl+C`) no longer kills the emulator by default (unless using `--attach`).
- 📄 Logs stored in `~/.android-proxy-logs`
- 🛠 Optional AVD launcher built-in
- 🎨 Clean, color-coded terminal output using the `rich` library

---

## 🛠️ Tools Used

| Tool         | Purpose                                           |
|--------------|---------------------------------------------------|
| `python3`    | Core automation scripting and UI handling         |
| `microsocks` | SOCKS5 proxy that forwards traffic from privoxy   |
| `privoxy`    | HTTP proxy that converts traffic to SOCKS5        |
| `adb`        | Android Debug Bridge to configure proxy settings  |
| `InquirerPy` | Interactive fuzzy multi-select command line UI    |
| `rich`       | Terminal color output and panel formatting        |

---

## 📥 Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/melkopisi/android-emulator-proxy.git
   cd android-emulator-proxy
   ```

2. **Run the installer**:
   The installer creates a secure, isolated Python `.venv` in `~/.local/share` to install required dependencies without hitting macOS `pip` blocks. It also creates a wrapper script in `~/.local/bin/androidproxy`.

   ```bash
   ./install.sh
   ```

*(Note: Ensure `~/.local/bin` is added to your `$PATH`! The installer will automatically try to append this to your `~/.zshrc`.)*

---

## 🟢 Usage

To start the proxy anytime, run:
```bash
androidproxy
```

### Additional Options:
- **Attach Emulator Lifecycle**: `androidproxy --attach` (or `-a`) to kill the emulator when you exit the proxy.
- **Clear Proxy Only**: `androidproxy --clear` to interactively select and clear proxy settings from running emulators without launching the proxy again.
- **Background, single instance**: `androidproxy up` starts it in the background (or does nothing if it's already healthy). Then `androidproxy status`, `restart`, `stop`, `logs`. See [Single instance, health checks and auto-restart](../README.md#single-instance-health-checks-and-auto-restart) for health checks, the menu bar status and the Android Studio setup.
---

## 🛑 Stopping

To stop the proxy services, press Ctrl+C. The script will:
<br />
	•	Kill microsocks and privoxy
 <br />
	•	Reset emulator’s proxy settings

 ---

 ## 📄 License

 [Apache License V2](LICENSE)
 
