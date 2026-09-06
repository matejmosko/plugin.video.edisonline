"""
Edisonline session configuration

This addon no longer targets a public REST API. The real traffic uses:
- same-origin content-detail routes at https://edisonline.sk/content/detail/...
- signed stream URLs at https://stream.moderntv.eu/stream.m3u8?...
- CDN HLS manifests and Widevine DRM metadata

This file keeps the browser OTT flow config in one place.
"""

REAL_API_CONFIG = {
    'base_url': 'https://edisonline.sk',
    'stream_base_url': 'https://stream.moderntv.eu',
    'stream_quality': 20,
    'default_headers': {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0',
        'Accept': '*/*',
        'Accept-Language': 'sk,cs;q=0.8,en-US;q=0.5,en;q=0.3',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Referer': 'https://edisonline.sk/',
    },
    'session_cookies': {
        # Non-sensitive generic defaults only. Authenticated cookies
        # (device_id, device_auth, PHPSESSID) are captured at login time
        # and persisted in session_cookies.json — never hardcode them.
        'streamQuality': '20',
        'timezone': 'Europe/Prague',
        'ott-registration': 'enabled',
        'ab_ott_registration': '1',
    },
    'timeouts': {
        'default': 30,
        'streaming': 60,
    },
}

def get_config():
    """Return the active browser-OTT config."""
    return REAL_API_CONFIG


def get_base_url():
    return get_config()['base_url']


def get_default_headers():
    return get_config()['default_headers'].copy()


def get_timeouts():
    return get_config()['timeouts'].copy()
