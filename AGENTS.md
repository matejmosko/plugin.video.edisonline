# AGENTS.md — Developer and Agent Guidance for `plugin.video.edisonline`

## Core Architecture & Context
- Kodi 22 (Piers) video addon for Edisonline with Widevine DRM support. Python 3.
- Browser-OTT flow based on Edisonline's web player (`https://edisonline.sk`).
- Login uses the web form proxy at `https://edisonline.sk/welcome/login`; the addon then persists the resulting browser session cookies.
- Session cookies carried in setting `session_cookie_json` and mirrored synchronously to `session_cookies.json`.

## Flow & Endpoints
- Login form proxy at `https://edisonline.sk/welcome/login` with CSRF `_token_` and `_do` action.
- Catalog category listings at `https://edisonline.sk/catalog/category/<slug>`.
- Content detail route at `https://edisonline.sk/content/detail/vodEntry:<id>`.
- Signed stream URLs at `https://stream.moderntv.eu/stream.m3u8?...` or regional CDN hosts.

## Project Structure
```
plugin.video.edisonline/
├── addon.xml                      # Kodi 22 addon manifest (requires inputstream.adaptive)
├── main.py                        # Entry point and URL router
├── api_config.py                  # Browser-OTT config (base URLs, headers, timeouts, non-secret cookie defaults)
├── resources/
│   ├── settings.xml               # User settings (email, password, debug, timeout)
│   ├── icon.png / fanart.jpg      # Addon assets
│   └── language/                  # Localization (en_gb, sk_sk)
└── lib/
    ├── __init__.py
    ├── _base.py                   # Shared addon singleton, log(), show_notification()
    ├── _http.py                   # HTTP transport layer (Session, CookieJar, build_multipart)
    ├── parsers.py                 # HTML/content parsing helpers + DRM URL constants
    ├── api.py                     # Edisonline API wrapper (login, catalog, detail, stream)
    ├── drm.py                     # Widevine DRM & inputstream.adaptive configuration
    └── router.py                  # URL routing & navigation logic
```

## Security
- **NEVER hardcode credentials** (email, password, `device_id`, `device_auth`, `PHPSESSID`, tokens) in source. Authenticated cookies must come from `session_cookies.json` / `session_cookie_json` only.
- Do not commit `.har` capture files, `session_cookies.json`, `settings.xml` with values, or credential dumps.
- Do not log sensitive data (tokens, passwords, session cookies) to the console.

## Key Conventions & Constraints
- **DO NOT** hardcode credentials — always use `addon.getSetting('email')` / `password`.
- **DO NOT** bypass Kodi's xbmc/xbmcgui APIs for UI — maintain addon-native look.
- **DO NOT** ignore `inputstream.adaptive` dependency in `addon.xml`.
- **DO NOT** log sensitive data (tokens, passwords) to console.
- **ONLY** use urllib3/requests for HTTP (no external streaming libraries).
- **ONLY** support Kodi 22+ — use Python 3.x syntax and xbmcvfs for file operations.
- **ALWAYS** wrap API calls in try-except and show errors via `xbmcgui.Dialog()`.
- **ALWAYS** validate manifest URLs and DRM license responses before playback.
- **ALWAYS** use `xbmcgui.Dialog().input()` — `xbmcgui.Keyboard` was removed in Kodi 22.
- **ALWAYS** strip leading `?` from query string before `parse_qs` (see `main.py:66`).

## Debug & Logging
- Debug logging controlled by `debug_logging` setting (boolean).
- Log prefix: `[Edisonline]` (main), `[Edisonline-Router]` (router), `[Edisonline-OTT]` (api).
- Use `xbmc.log(message, level)` with `xbmc.LOGINFO` / `xbmc.LOGERROR` / `xbmc.LOGWARNING`.

## Entry Point (main.py)
- Called with `sys.argv[0]` (base URL), `sys.argv[1]` (handle), `sys.argv[2]` (query string).
- Parses `action` parameter: `main`, `movies`, `genres`, `recommendations`, `search`, `play`.
- Imports `Router` from `lib.router` with fallback dynamic import.

## Router (lib/router.py)
- `Router(handle, base_url)` encapsulates navigation.
- Uses `api.EdisononlineAPI()` and `drm.DRMHandler()`.
- Every public method checks `_require_session()` first.
- `_require_session()`: validates session, attempts auto-login, prompts credentials if needed.
- URL building via `_build_url(**kwargs)` using `urlencode`.
- Menu items use `addon.getLocalizedString(id)` for i18n.

## API Module (lib/api.py)
- `EdisononlineAPI` class: browser session, cookie handling, endpoints.
- Imports shared helpers from `_base`, `_http` (`Session`), `parsers`, and `api_config` getters.
- `login(email, password)`: form POST with CSRF token extraction; persists authenticated cookies on success.
- `get_movies(page, limit, genre)`, `get_genres()`, `get_recommendations()`, `search(query)`.
- `get_movie_detail(movie_id)` → returns stream URL for DRM.
- Session persistence: `session_cookie_store` dict + file `session_cookies.json` in profile dir.
- `_play_session()` builds a streaming session from the persisted authenticated cookies (no hardcoded credentials).

## DRM Module (lib/drm.py)
- `DRMHandler`: configures `inputstream.adaptive` for Widevine.
- `get_list_item_from_movie(movie)`: builds `ListItem` with DRM properties.
- `resolve_stream(movie_id, title)`: gets stream URL, sets license headers, returns resolved ListItem.
- `_set_movie_info(info_tag, data)`: shared helper tags movie metadata (plot, year, cast, duration) on a `VideoInfoTag`.
- Required properties: `inputstream.adaptive.manifest_type`, `license_type`, `license_key`.

## Helpers (lib/_base.py & lib/_http.py & lib/parsers.py)
- `_base.py`: module-level `addon` singleton + `log()` + `show_notification()`; all modules use it instead of creating their own `Addon()`.
- `_http.py`: `Session`/`CookieJar`/`Response` wrappers around urllib3-style access, `build_multipart(fields)` for form POSTs, gzip handling, no-redirect policy.
- `parsers.py`: `clean_stream_url()`, `extract_stream_url()`, `extract_license_url()`, `parse_catalog_page()`, `parse_search_page()`, `parse_modal_metadata()`; owns `WIDEVINE_LICENSE_URL` constant. No request logic lives here.

## Configuration (api_config.py)
- Single `REAL_API_CONFIG` plus getters `get_config()`, `get_base_url()`, `get_default_headers()`, `get_timeouts()`.
- Headers: User-Agent, Accept, Accept-Language, Accept-Encoding (gzip, deflate), Referer.
- `session_cookies` defaults contain **non-secret** generic cookies only (streamQuality, timezone, ott-registration, ab_ott_registration). Authenticated cookies are never defined here.

## Dependencies
- `xbmc.python` 3.0.0+
- `inputstream.adaptive` 22.0.0+ (for HLS/MPEG-DASH + Widevine)

## Testing Notes
- Run inside Kodi 22+ environment (flatpak: `~/.var/app/tv.kodi.Kodi/`).
- Addon data: `~/.var/app/tv.kodi.Kodi/data/userdata/addon_data/plugin.video.edisonline/`.
- Logs: `~/.var/app/tv.kodi.Kodi/data/temp/kodi.log`.
- For quick syntax check: `python3 -m py_compile main.py api_config.py lib/_base.py lib/_http.py lib/parsers.py lib/api.py lib/router.py lib/drm.py`.