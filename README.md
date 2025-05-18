# Android Emulator Proxy Tool for macOS

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
- 📜 Installer adds everything you need: dependencies, paths, aliases
- ⚙️ Auto-kills previous proxy instances to avoid port conflicts
- 📄 Logs stored in `~/.android-proxy-logs`
- 🛠 Optional AVD launcher built-in
- 🎨 Clean, color-coded terminal output

---

## 🛠️ Tools Used

| Tool         | Purpose                                           |
|--------------|---------------------------------------------------|
| `microsocks` | SOCKS5 proxy that forwards traffic from privoxy   |
| `privoxy`    | HTTP proxy that converts traffic to SOCKS5        |
| `adb`        | Android Debug Bridge to configure proxy settings  |
| `bash`       | Shell scripting for automation                    |
| `tput`       | Terminal color output for better UX               |

---

## 📥 Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/melkopisi/android-emulator-proxy.git
   cd android-emulator-proxy

---

## 🟢 Usage

To start the proxy anytime, run:
```bash
androidproxy
```
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
 
