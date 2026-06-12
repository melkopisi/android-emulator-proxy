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

## License

[Apache License V2](LICENSE)
