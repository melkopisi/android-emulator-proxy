# Android Emulator MITM Proxy

An advanced solution using `mitmproxy` to intercept, view, and debug HTTPS traffic from Android emulators on macOS.

## ✅ Solution

This tool sets up a **local proxy** on your Mac using `mitmproxy` and automatically configures your selected Android Emulator to route its traffic through it.

- **mitmproxy / mitmweb**: Captures and decrypts HTTPS traffic for inspection.
- **ADB Configuration**: Automatically sets the emulator's internal proxy to forward traffic to `mitmproxy`.

## 📦 Features

- 🔄 Routes and intercepts emulator HTTPS traffic.
- 🎯 **Fuzzy Multi-Select UI**: Beautiful interactive prompt using `InquirerPy` to select emulators.
- 🌐 **Web Dashboard**: View traffic cleanly in your browser.
- 🔍 **Domain Filtering**: Only intercept traffic for specific domains (e.g. your API) while ignoring background OS noise.
- 📜 **Isolated Installer**: Installs dependencies safely via a lightweight `.venv`.

## 📥 Installation

1. **Navigate to the directory**:
   ```bash
   cd mitm
   ```

2. **Run the installer**:
   ```bash
   ./install.sh
   ```
   *This creates a secure `.venv` and a global command `androidmitm` in `~/.local/bin`.*

## 🟢 Usage

Start the proxy from anywhere:

```bash
# Terminal UI (Default)
androidmitm

# Web UI (Recommended for better visibility, especially for RTL/Arabic text)
androidmitm -w
# or
androidmitm --web

# Filter specific domains (e.g., only intercept example.com and myapi.com)
androidmitm -f example.com myapi.com
```

### Android Configuration Requirement

To intercept HTTPS traffic on newer Android versions without rooting your emulator, your Android app must be configured to trust User certificates in debug mode.

Add this to your app's `network_security_config.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <base-config cleartextTrafficPermitted="true">
        <trust-anchors>
            <certificates src="system" />
        </trust-anchors>
    </base-config>

    <debug-overrides>
        <trust-anchors>
            <certificates src="user" />
        </trust-anchors>
    </debug-overrides>
</network-security-config>
```

And reference it in your `AndroidManifest.xml`:
```xml
<application android:networkSecurityConfig="@xml/network_security_config" ...>
```
