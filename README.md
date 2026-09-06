# plugin.video.edisonline

Kodi 22 (Piers) video addon for Edisonline streaming with Widevine DRM support.

## Disclaimer
This project is an **unofficial, community addon** and is **not affiliated with, endorsed by, or sponsored by Edisonline** (or its parent companies). All product names, logos, and brands are property of their respective owners.

The addon interacts with publicly available web endpoints of the service for the purpose of personal, private use. Use it **at your own risk**:

- Streaming content or circumventing technical protection measures may violate the service's Terms of Service or applicable law in your jurisdiction.
- The project is provided **"as is"**, without warranty of any kind. The author is not liable for any damages, account suspensions, or other consequences arising from its use.
- Widevine DRM and playback integration is provided solely to allow playback of content you are legally entitled to watch.

## Features
- Browse movies by category and recommendations
- Search the Edisonline VOD catalog
- Play HLS/MPEG-DASH streams with `inputstream.adaptive` and Widevine DRM
- Browser-session based authentication with persisted cookies (`session_cookies.json`)
- Slovak (`sk_sk`) and English (`en_gb`) localization

## Requirements
- Kodi 22 (Piers) — Python 3.x
- Dependencies installed automatically: `xbmc.python` 3.0.0+, `inputstream.adaptive` 22.0.0+

## Installation
1. Copy the `plugin.video.edisonline` folder into your Kodi addons directory:
   - Flatpak: `~/.var/app/tv.kodi.Kodi/data/addons/`
   - Standard Linux: `~/.kodi/addons/`
2. Restart Kodi (or enable the addon in **Add-ons** → **My add-ons** → **Video**).
3. Open the addon and configure your credentials under **Settings** (`email`, `password`).
4. Navigate to any movie to authenticate the session and start playback.

## Configuration
- `email` / `password` — Edisonline account credentials (stored in Kodi's setting store, never in source).
- `debug_logging` — enable `[Edisonline]` debug logging for troubleshooting.
- `timeout` — HTTP request timeout in seconds.

## Project Structure
```
plugin.video.edisonline/
├── addon.xml                      # Kodi 22 addon manifest (requires inputstream.adaptive)
├── main.py                        # Entry point and URL router
├── api_config.py                  # Browser-OTT config (base URLs, headers, timeouts, cookie defaults)
├── resources/
│   ├── settings.xml               # User settings (email, password, debug, timeout)
│   ├── icon.png / fanart.jpg      # Addon assets
│   └── language/                  # Localization (en_gb, sk_sk)
└── lib/
    ├── _base.py                   # Shared addon singleton, log(), show_notification()
    ├── _http.py                   # HTTP transport layer (Session, CookieJar, build_multipart)
    ├── parsers.py                 # HTML/content parsing helpers + DRM URL constants
    ├── api.py                     # Edisonline API wrapper (login, catalog, detail, stream)
    ├── drm.py                     # Widevine DRM & inputstream.adaptive configuration
    └── router.py                  # URL routing & navigation logic
```

## How It Works
- The addon logs in through Edisonline's web form proxy (`https://edisonline.sk/welcome/login`) and captures the resulting browser session cookies.
- Authenticated cookies are persisted to `session_cookies.json` in the addon's profile directory and used for catalog browsing, stream signing, and DRM license requests.
- Playback uses `inputstream.adaptive` with Widevine license headers resolved from the content detail page.

## Security
- Credentials are never hardcoded in source. Authenticated cookies (`PHPSESSID`, `device_id`, `device_auth`) are captured at login and stored in the addon's profile directory only.
- Debug logging never prints tokens, passwords, or session cookies.

## Debugging
- Logs: `~/.var/app/tv.kodi.Kodi/data/temp/kodi.log` (flatpak) or `~/.kodi/temp/kodi.log`.
- Addon data: `~/.var/app/tv.kodi.Kodi/data/userdata/addon_data/plugin.video.edisonline/`.