"""
Edisonline browser-OTT API wrapper.

This implementation matches the real browser flow discovered from the site:
- same-origin content detail fetches from https://edisonline.sk/content/detail/...
- signed stream URLs from https://stream.moderntv.eu/stream.m3u8?...
- CDN redirect to HLS manifest with Widevine metadata

It does not rely on a public REST API.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse

import xbmc

from _base import addon, log as _base_log
from _http import Session, build_multipart
from parsers import (
    WIDEVINE_LICENSE_URL, _WIDEVINE_KEYFORMAT, _DRM_URL_MARKERS,
    clean_stream_url, extract_stream_url, extract_license_url,
    parse_catalog_page, parse_search_page, parse_modal_metadata,
)

_ADDON_ID = 'plugin.video.edisonline'
_DEFAULT_DATA_DIR = os.path.join(
    os.path.expanduser('~'), '.var', 'app', 'tv.kodi.Kodi',
    'data', 'userdata', 'addon_data', _ADDON_ID,
)


def _addon_data_dir():
    """Return the addon profile directory, creating it if needed."""
    for _try in range(3):
        try:
            import xbmcvfs
            profile = addon.getAddonInfo('profile')
            if profile:
                path = xbmcvfs.translatePath(profile)
                if path and '://' not in path:
                    os.makedirs(path, exist_ok=True)
                    return path
        except Exception:
            pass
        try:
            profile = addon.getAddonInfo('profile')
            if profile:
                path = xbmc.translatePath(profile)
                if path and '://' not in path:
                    os.makedirs(path, exist_ok=True)
                    return path
        except Exception:
            pass
        break
    os.makedirs(_DEFAULT_DATA_DIR, exist_ok=True)
    return _DEFAULT_DATA_DIR


def _session_file():
    """Absolute path to the on-disk session cookie file."""
    return os.path.join(_addon_data_dir(), 'session_cookies.json')


def _detail_cache_file():
    """Absolute path to the persistent movie detail cache."""
    return os.path.join(_addon_data_dir(), 'movie_details_cache.json')


try:
    from api_config import get_base_url, get_default_headers, get_timeouts, get_config
except ImportError:
    get_base_url = lambda: 'https://edisonline.sk'  # type: ignore
    get_default_headers = lambda: {'User-Agent': 'Mozilla/5.0'}  # type: ignore
    get_timeouts = lambda: {'default': 30, 'streaming': 60}  # type: ignore
    get_config = lambda: {'session_cookies': {}}  # type: ignore


class EdisononlineAPI:
    """Wrapper around the real Edisonline OTT web flow."""

    def __init__(self):
        self.base_url = get_base_url()
        self.timeout = int(addon.getSetting('timeout') or get_timeouts()['default'])
        self.debug = addon.getSetting('debug_logging') == 'true'
        self.session = Session()
        self.session.headers.update(get_default_headers())
        self.public_session = Session()
        self.public_session.headers.update(get_default_headers())
        self.session_cookie_store = {}
        self._session_invalid = False
        self._detail_cache = {}
        self._load_detail_cache_from_disk()
        self._load_session_from_settings()

    def _log(self, message, level=xbmc.LOGINFO):
        if self.debug:
            xbmc.log(f'[Edisonline-OTT] {message}', level)

    # -- session / cache persistence ------------------------------------------

    def _load_session_from_settings(self):
        """Load browser session cookies (from session file, then settings)."""
        payload = {}
        try:
            with open(_session_file(), 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                payload = data
        except Exception:
            payload = {}
        if not payload:
            raw = addon.getSetting('session_cookie_json')
            if raw:
                try:
                    data = json.loads(raw)
                    if isinstance(data, dict):
                        payload = data
                except Exception:
                    payload = {}
        self.session_cookie_store = payload
        self._log(f'_load_session_from_settings: file={_session_file()} '
                  f'store={len(payload)} jar={len(self.session.cookies)}')
        if self.session_cookie_store:
            for key, value in self.session_cookie_store.items():
                if value is not None:
                    self.session.cookies.set(key, str(value),
                                             domain='edisonline.sk', path='/')

    def _persist_session(self):
        path = _session_file()
        tmp = path + '.tmp'
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(self.session_cookie_store, fh)
            os.replace(tmp, path)
            self._log(f'_persist_session: wrote {len(self.session_cookie_store)} '
                      f'cookies to {path}')
        except Exception as exc:
            self._log(f'_persist_session: FILE WRITE FAILED {path}: {exc}',
                      xbmc.LOGERROR)
        try:
            addon.setSetting('session_cookie_json',
                             json.dumps(self.session_cookie_store))
        except Exception:
            pass

    def _load_detail_cache_from_disk(self):
        path = _detail_cache_file()
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, dict):
                        self._detail_cache[str(k)] = v
            self._log(f'_load_detail_cache_from_disk: '
                      f'{len(self._detail_cache)} entries from {path}')
        except Exception:
            self._detail_cache = {}

    def _persist_detail_cache(self):
        path = _detail_cache_file()
        tmp = path + '.tmp'
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(self._detail_cache, fh, ensure_ascii=False)
            os.replace(tmp, path)
            self._log(f'_persist_detail_cache: {len(self._detail_cache)} '
                      f'entries to {path}')
        except Exception as exc:
            self._log(f'_persist_detail_cache: FILE WRITE FAILED {path}: {exc}',
                      xbmc.LOGWARNING)

    def get_cached_detail(self, movie_id):
        if not movie_id:
            return None
        key = self._normalize_id(movie_id)
        cached = self._detail_cache.get(key)
        return dict(cached) if isinstance(cached, dict) else None

    def _clear_detail_cache(self):
        count = len(self._detail_cache)
        self._detail_cache.clear()
        path = _detail_cache_file()
        try:
            if os.path.exists(path):
                os.remove(path)
                self._log(f'_clear_detail_cache: removed {count} entries from {path}')
            else:
                self._log(f'_clear_detail_cache: cleared {count} in-memory entries')
        except Exception as exc:
            self._log(f'_clear_detail_cache: failed to remove {path}: {exc}',
                      xbmc.LOGERROR)

    # -- preload / session checks ---------------------------------------------

    def preload_visible_details(self, movies, count=20):
        if not movies:
            return
        fetched = 0
        seen = set()
        for movie in movies[:count]:
            mid = movie.get('id')
            if not mid:
                continue
            raw_key = self._normalize_id(mid)
            if raw_key in seen or raw_key in self._detail_cache:
                continue
            seen.add(raw_key)
            try:
                self.get_movie_detail(mid)
                fetched += 1
            except Exception as exc:
                self._log(f'preload_visible_details: error fetching {mid}: {exc}',
                          xbmc.LOGWARNING)
        self._log(f'preload_visible_details: fetched={fetched} '
                  f'cache_size={len(self._detail_cache)}')

    def is_session_ready(self):
        if self._session_invalid:
            return False
        return bool(
            self.session.cookies.get('PHPSESSID')
            or self.session_cookie_store.get('PHPSESSID')
            or self.session.cookies.get('device_id')
            or self.session_cookie_store.get('device_id')
        )

    def needs_relogin(self):
        """True when the server redirected playback to the login page,
        meaning the persisted session cookies are stale/invalid."""
        return bool(getattr(self, '_session_invalid', False))

    def invalidate_session(self):
        """Drop the stale OTT session everywhere (memory, disk, settings).

        The next call to _require_session() will auto-login with the saved
        email/password and persist a fresh authenticated cookie set.
        """
        self._session_invalid = True
        self.session_cookie_store.clear()
        try:
            self.session.cookies.clear()
        except Exception as exc:
            self._log(f'invalidate_session: cookie clear failed: {exc}',
                      xbmc.LOGWARNING)
        try:
            if os.path.exists(_session_file()):
                os.remove(_session_file())
        except OSError as exc:
            self._log(f'invalidate_session: file remove failed: {exc}',
                      xbmc.LOGWARNING)
        try:
            addon.setSetting('session_cookie_json', '')
        except Exception:
            pass
        self._log('invalidate_session: cleared stale session cookies')

    def _is_login_redirect(self, url):
        path = urllib.parse.urlparse(url).path
        return path.startswith('/profile') or path.startswith('/welcome/login')

    def ensure_session_ready(self):
        if not self.is_session_ready():
            self._log('OTT browser session is not ready; '
                      'session cookies are required.', xbmc.LOGWARNING)
            return False
        return True

    # -- HTTP helpers ---------------------------------------------------------

    def _default_headers(self):
        headers = get_default_headers().copy()
        headers.update({
            'X-Requested-With': 'XMLHttpRequest',
            'Origin': 'https://edisonline.sk',
            'Sec-Fetch-Site': 'same-origin',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
        })
        return headers

    def _request(self, method, url, params=None, data=None, headers=None,
                 allow_redirects=True, timeout=None, session=None):
        req_headers = self._default_headers()
        if headers:
            req_headers.update(headers)
        s = session or self.session
        try:
            response = s.request(method, url, params=params, data=data,
                                 headers=req_headers,
                                 allow_redirects=allow_redirects,
                                 timeout=timeout or self.timeout)
            response.raise_for_status()
            return response
        except urllib.error.HTTPError as exc:
            self._log(f'HTTP error for {url}: {exc}', xbmc.LOGERROR)
            return None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self._log(f'Network error for {url}: {exc}', xbmc.LOGERROR)
            return None

    def _ensure_public_session(self):
        if not self.public_session.cookies.get('PHPSESSID'):
            try:
                r = self._request('GET', f'{self.base_url}/welcome/login',
                                  session=self.public_session)
                if (r and r.status_code < 400
                        and self.public_session.cookies.get('PHPSESSID')):
                    self._log('public session primed with fresh anonymous PHPSESSID')
            except Exception as exc:
                self._log(f'failed to prime public session: {exc}', xbmc.LOGERROR)

    def _public_request(self, method, url, params=None, data=None, headers=None,
                        allow_redirects=True, timeout=None):
        self._ensure_public_session()
        return self._request(method, url, params=params, data=data,
                             headers=headers, allow_redirects=allow_redirects,
                             timeout=timeout, session=self.public_session)

    # -- extraction helpers (delegating to parsers) ---------------------------

    def _extract_stream_url(self, text):
        return extract_stream_url(text, log=self._log)

    def _extract_license_url(self, text):
        return extract_license_url(text)

    def _extract_license_from_hls(self, manifest_url):
        try:
            self._log(f'_extract_license_from_hls: fetching '
                      f'{manifest_url[:100]}...')
            response = self._public_request('GET', manifest_url)
            if not response:
                return None
            manifest = response.text
            self._log(f'_extract_license_from_hls: manifest len={len(manifest)}, '
                      f'content={manifest[:500]}')
            key_patterns = [
                r'#EXT-X-KEY:[^#]*URI="([^"]+)"[^#]*KEYFORMAT="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"',
                r'#EXT-X-KEY:[^#]*URI="([^"]+)"[^#]*KEYFORMAT="com\.widevine\.alpha"',
                r'#EXT-X-KEY:[^#]*URI="([^"]+)"[^#]*METHOD=SAMPLE-AES',
                r'#EXT-X-KEY:[^#]*URI="([^"]+)"',
            ]
            for pattern in key_patterns:
                match = re.search(pattern, manifest, re.I)
                if match:
                    license_url = clean_stream_url(match.group(1))
                    if (_WIDEVINE_KEYFORMAT in match.group(0)
                            or 'com.widevine.alpha' in match.group(0)):
                        self._log('_extract_license_from_hls: Widevine content '
                                  '-> platform license URL')
                        return WIDEVINE_LICENSE_URL
                    if not license_url.startswith('data:') and license_url:
                        self._log(f'_extract_license_from_hls: found license URL: '
                                  f'{license_url[:100]}...')
                        return license_url
                    if _WIDEVINE_KEYFORMAT in manifest:
                        return WIDEVINE_LICENSE_URL
            if 'EXT-X-KEY' in manifest:
                self._log(f'_extract_license_from_hls: EXT-X-KEY found but no URI '
                          f'matched: {manifest[:1000]}', xbmc.LOGWARNING)
            else:
                self._log('_extract_license_from_hls: no EXT-X-KEY in manifest '
                          '(clear stream)')
        except Exception as e:
            self._log(f'_extract_license_from_hls error: {e}', xbmc.LOGWARNING)
        return None

    # -- content detail parsing -----------------------------------------------

    def _parse_content_detail(self, body):
        if not body:
            self._log('_parse_content_detail: empty body', xbmc.LOGWARNING)
            return {}

        if isinstance(body, dict):
            data = body
        else:
            try:
                data = json.loads(body)
            except Exception:
                data = {'raw_text': body}

        title = stream_url = license_url = backdrop = modal_html = None

        if isinstance(data, dict):
            snippets = data.get('snippets', {})
            modal_html = (snippets.get('snippet--pageModal')
                          if isinstance(snippets, dict) else None)
            if modal_html:
                self._log(f'_parse_content_detail: found modal_html, '
                          f'len={len(modal_html)}')
                stream_url = self._extract_stream_url(modal_html)
                license_url = self._extract_license_url(modal_html)
                match = re.search(r'<h1[^>]*>(.*?)</h1>', modal_html, re.S | re.I)
                if match:
                    title = ' '.join(
                        re.sub(r'<.*?>', '', match.group(1)).split())
                    self._log(f'_parse_content_detail: found title={title}')
                match = re.search(r'data-url="([^"]+)"', modal_html, re.I)
                if match:
                    backdrop = match.group(1)
                if not stream_url:
                    stream_url = self._extract_stream_url(json.dumps(data))
                if not license_url:
                    license_url = self._extract_license_url(json.dumps(data))
            else:
                self._log(f'_parse_content_detail: no snippet--pageModal. '
                          f'Keys: {list(snippets.keys()) if isinstance(snippets, dict) else "n/a"}',
                          xbmc.LOGWARNING)

        if not title:
            title = data.get('title') if isinstance(data, dict) else None
        if not stream_url and isinstance(data, dict):
            stream_url = self._extract_stream_url(json.dumps(data))
        if not license_url and isinstance(data, dict):
            license_url = self._extract_license_url(json.dumps(data))

        meta = parse_modal_metadata(modal_html, log=self._log) if modal_html else {}

        self._log(f'_parse_content_detail: title={title}, '
                  f'stream_url={"found" if stream_url else "none"}, '
                  f'license_url={"found" if license_url else "none"}')

        meta.update({
            'title': title or meta.get('title') or 'Edisonline title',
            'stream_url': stream_url,
            'license_url': license_url,
            'backdrop': backdrop,
            'raw': data,
        })
        if stream_url and not meta.get('trailer_url'):
            meta['trailer_url'] = stream_url

        meta['requires_purchase'] = (
            bool(modal_html)
            and '>Kúpiť</span>' in modal_html
            and '>Prehrať</span>' not in modal_html
        )
        if meta['requires_purchase']:
            self._log('_parse_content_detail: movie needs purchase '
                      '(Kúpiť button, no Prehrať)', xbmc.LOGWARNING)

        return meta

    # -- movie detail ---------------------------------------------------------

    def get_movie_detail(self, movie_id, session=None, force=False):
        if not movie_id:
            return None
        raw_key = self._normalize_id(movie_id)
        if not force and raw_key in self._detail_cache:
            self._log(f'get_movie_detail: cache hit for {raw_key}')
            return dict(self._detail_cache[raw_key])

        url = f'{self.base_url}/content/detail/vodEntry:{raw_key}'
        s = session or self.public_session
        self._log(f'get_movie_detail: fetching {url} with public session')
        response = self._public_request(
            'GET', url,
            headers={'x-ajax-mode': 'modal',
                     'Referer': f'{self.base_url}/vod'})
        if not response:
            self._log(f'get_movie_detail: request failed for {url}', xbmc.LOGERROR)
            return None

        self._log(f'get_movie_detail: status={response.status_code}, '
                  f'len={len(response.content)}')
        try:
            payload = response.json()
        except ValueError:
            self._log(f'get_movie_detail: invalid JSON: {response.text[:200]}',
                      xbmc.LOGERROR)
            payload = {'raw_text': response.text}

        detail = self._parse_content_detail(payload)
        if detail and raw_key:
            self._detail_cache[raw_key] = detail
            self._persist_detail_cache()
        return detail

    # -- stream URL resolution ------------------------------------------------

    def get_stream_url(self, movie_id, quality='auto'):
        movie_id = self._normalize_id(movie_id)
        stream_url, requires_purchase = self._get_movie_stream_url(movie_id)
        if stream_url:
            info = self._process_stream_url(stream_url, movie_id, quality)
            if info is not None:
                info['requires_purchase'] = False
            return info
        return {'stream_url': None, 'manifest_url': None, 'license_url': None,
                'drm_system': None, 'requires_purchase': bool(requires_purchase)}

    def _normalize_id(self, movie_id):
        if not movie_id:
            return None
        s = str(movie_id)
        return s.split(':')[-1] if ':' in s else s

    def _play_session(self):
        sess_cookies = dict(self.session_cookie_store)
        cfg = (get_config() or {}).get('session_cookies', {}) or {}
        for key, value in cfg.items():
            sess_cookies.setdefault(key, value)
        s = Session()
        s.headers.update(get_default_headers())
        for k, v in sess_cookies.items():
            if v is not None:
                s.cookies.set(str(k), str(v), domain='edisonline.sk', path='/')
        return s

    def _get_movie_stream_url(self, movie_id):
        if not movie_id:
            return None, False
        raw_id = self._normalize_id(movie_id)
        play_session = self._play_session()
        play_url = f'{self.base_url}/vod/play?entryId={raw_id}'
        self._log(f'_get_movie_stream_url: GET {play_url}')
        response = self._request(
            'GET', play_url,
            headers={'Referer': f'{self.base_url}/vod',
                     'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8'},
            allow_redirects=False, session=play_session)
        if not response:
            self._log('_get_movie_stream_url: request failed', xbmc.LOGWARNING)
            return None, False

        body = response.text
        seen = set()
        for _ in range(8):
            nxt = None
            if response.status_code in (301, 302, 303, 307, 308):
                nxt = response.headers.get('Location')
            if not nxt and body.strip().startswith('{'):
                try:
                    j = json.loads(body)
                    if isinstance(j, dict) and j.get('redirect'):
                        nxt = j['redirect']
                except Exception:
                    nxt = None
            if not nxt:
                break
            nxt = urllib.parse.urljoin(self.base_url, nxt)
            if nxt in seen:
                break
            seen.add(nxt)
            if self._is_login_redirect(nxt):
                self._session_invalid = True
                self._log('session invalid: redirected to login/profile during '
                          'stream resolution', xbmc.LOGWARNING)
            self._log(f'_get_movie_stream_url: following redirect -> {nxt}')
            response = self._request('GET', nxt,
                                     headers={'Referer': f'{self.base_url}/vod'},
                                     allow_redirects=False,
                                     session=play_session)
            if not response:
                break
            body = response.text

        self._log(f'_get_movie_stream_url: status='
                  f'{response.status_code if response else "None"} '
                  f'len={len(body)} profile={"profile" in body[:80]}')

        movie_url = self._movie_stream_url_from_playlist(body)
        if movie_url:
            return movie_url, False

        if self._trailer_only_page(body):
            self._log(f'_get_movie_stream_url: only trailer for {raw_id}',
                      xbmc.LOGWARNING)
            return None, self._purchase_required(body)

        stream_url = self._extract_stream_url(body)
        if stream_url:
            self._log(f'_get_movie_stream_url: extracted stream URL: '
                      f'{stream_url[:100]}...')
            return stream_url, False

        self._log('_get_movie_stream_url: no stream URL found', xbmc.LOGWARNING)
        return None, self._purchase_required(body)

    def _purchase_required(self, body):
        if not body:
            return False
        return 'lucide:shopping-cart' in body or '>Kúpiť</span>' in body

    def _trailer_only_page(self, body):
        if not body:
            return False
        if 'playTrailer(' in body:
            return True
        return bool(re.search(r'stream\.m3u8[^"\'\\ ]*vod-event-id=(\d+)', body))

    def _movie_stream_url_from_playlist(self, body):
        if not body:
            return None
        pm = re.search(r'<script[^>]*x-ref="playlist"[^>]*>(.*?)</script>',
                       body, re.S)
        if not pm:
            pm = re.search(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
                           body, re.S)
        if not pm:
            return None
        try:
            data = json.loads(pm.group(1).strip())
        except Exception:
            self._log('_movie_stream_url_from_playlist: JSON parse failed',
                      xbmc.LOGWARNING)
            return None
        if not isinstance(data, dict):
            return None
        best_url = None
        best_dur = 0
        for evid, ev in data.items():
            if not isinstance(ev, dict):
                continue
            url = ev.get('streamUrl')
            if not url or 'stream.m3u8' not in url:
                continue
            try:
                dur = int(ev.get('duration', 0) or 0)
            except Exception:
                dur = 0
            if dur > best_dur:
                best_dur = dur
                best_url = url
        if best_url and best_dur > 300:
            self._log(f'_movie_stream_url_from_playlist: movie dur={best_dur}s: '
                      f'{best_url[:100]}')
            return clean_stream_url(best_url)
        if best_url:
            self._log(f'_movie_stream_url_from_playlist: only dur={best_dur}s',
                      xbmc.LOGWARNING)
        return None

    def _process_stream_url(self, stream_url, movie_id, quality):
        if not stream_url:
            self._log(f'_process_stream_url: no stream URL for {movie_id}',
                      xbmc.LOGWARNING)
            return None

        self._log(f'_process_stream_url: requesting {stream_url[:100]}...')
        redirect = self._request('GET', stream_url, allow_redirects=False)
        if redirect:
            self._log(f'_process_stream_url: redirect status={redirect.status_code}')
        if redirect is None:
            return {'manifest_url': stream_url, 'manifest_type': 'hls',
                    'stream_url': stream_url, 'license_url': None,
                    'drm_system': None, 'quality': 'auto', 'title': ''}

        final_manifest = redirect.headers.get('location') or stream_url

        license_url = self._extract_license_from_hls(final_manifest)
        if license_url:
            self._log(f'_process_stream_url: license from HLS: {license_url[:100]}...')
        else:
            marked = (final_manifest or '').lower() + ' ' + (stream_url or '').lower()
            if (any(m.lower() in marked for m in _DRM_URL_MARKERS)
                    or 'drm-system=widevine' in marked):
                license_url = WIDEVINE_LICENSE_URL
                self._log(f'_process_stream_url: DRM markers -> {license_url}')

        return {'manifest_url': final_manifest, 'manifest_type': 'hls',
                'stream_url': stream_url, 'license_url': license_url,
                'drm_system': 'widevine' if license_url else None,
                'quality': 'auto', 'title': ''}

    # -- catalog / search / genres --------------------------------------------

    def _parse_catalog_page(self, html):
        return parse_catalog_page(html, log=self._log)

    def _parse_search_page(self, html):
        return parse_search_page(html, log=self._log)

    def _fetch_catalog(self, path, modal=True):
        url = f'{self.base_url}{path}'
        if modal and path.startswith('/catalog/category/'):
            response = self._public_request('GET', url, headers={
                'x-ajax-mode': 'modal',
                'X-Requested-With': 'XMLHttpRequest',
                'Referer': f'{self.base_url}/vod'})
            if response is None:
                self._log(f'_fetch_catalog modal failed: {path}', xbmc.LOGERROR)
            if response:
                try:
                    payload = json.loads(response.text)
                except ValueError:
                    payload = {}
                snippets = (payload.get('snippets', {})
                            if isinstance(payload, dict) else {})
                if (isinstance(snippets, dict)
                        and snippets.get('snippet--pageModal')):
                    movies = self._parse_catalog_page(
                        snippets['snippet--pageModal'])
                    if movies:
                        return movies
                    self._log('_fetch_catalog modal had no cards - '
                              'falling back to full page', xbmc.LOGWARNING)
                else:
                    self._log(f'_fetch_catalog modal had no pageModal '
                              f'(state={payload.get("state")!r})',
                              xbmc.LOGWARNING)
        response = self._public_request('GET', url, headers={
            'Referer': f'{self.base_url}/vod'})
        if response is None:
            self._log(f'_fetch_catalog full-page failed: {path}', xbmc.LOGERROR)
            return None
        self._log(f'_fetch_catalog full-page {path}: status={response.status_code} '
                  f'len={len(response.content)}')
        return self._parse_catalog_page(response.text)

    def get_movies(self, page=1, limit=20, genre=None):
        if isinstance(genre, str) and genre.strip().lower() in ('none', 'null', 'false', ''):
            genre = None
        path = f'/catalog/category/{genre}' if genre else '/catalog/vod'
        movies = self._fetch_catalog(path)
        if movies is None:
            return None
        total = len(movies)
        if limit and limit > 0:
            total_pages = max(1, (total + limit - 1) // limit)
            page = max(1, int(page or 1))
            start = (page - 1) * limit
            movies = movies[start:start + limit]
        else:
            total_pages = 1
        return {'movies': movies, 'total_pages': total_pages, 'total': total}

    def get_genres(self):
        response = self._public_request('GET', f'{self.base_url}/catalog/vod',
                                        headers={'Referer': f'{self.base_url}/'})
        if not response:
            return None
        genres = []
        seen = set()
        for slug, label in re.findall(
                r'data-splide-category="(tvTipsCategory:[a-z0-9-]+)"'
                r'.{0,600}?aria-label="([^"]*)"',
                response.text, re.S):
            label = ' '.join(label.split())
            if not label or slug in seen:
                continue
            seen.add(slug)
            genres.append({'id': slug, 'name': label})
        return {'genres': genres}

    def search(self, query, page=1, limit=20):
        if not self.ensure_session_ready():
            return None
        if not query:
            return {'results': []}
        content_type, body = build_multipart({
            'searchId': __import__('uuid').uuid4().hex,
            'query': query,
            '_do': 'mainMenuSearch-search-submit',
        })
        response = self._public_request('POST', f'{self.base_url}/vod',
                                        data=body, headers={
                'Content-Type': content_type,
                'X-Requested-With': 'XMLHttpRequest',
                'Referer': f'{self.base_url}/vod'})
        if not response:
            self._log('search: request failed', xbmc.LOGERROR)
            return None
        self._log(f'search: status={response.status_code} '
                  f'len={len(response.content)}')
        try:
            payload = json.loads(response.text)
        except ValueError:
            return {'results': []}
        if isinstance(payload, dict) and payload.get('redirect'):
            self._log(f"search: redirect -> {payload.get('redirect')}",
                      xbmc.LOGWARNING)
            return {'results': []}
        snippets = (payload.get('snippets', {})
                    if isinstance(payload, dict) else {})
        html = (snippets.get('snippet-mainMenuSearch-results', '')
                if isinstance(snippets, dict) else '')
        results = self._parse_search_page(html)
        return {'results': results[:limit] if limit else results}

    def get_recommendations(self, limit=20):
        movies = self._fetch_catalog('/catalog/category/tvTipsCategory:recommended')
        if movies is None:
            return None
        return {'recommendations': movies[:limit]}

    def _navigate_headers(self):
        """Headers for real page navigations (form login, profile switch).

        The server answers the login POST differently when it detects an
        AJAX request (X-Requested-With present): it returns a tiny JSON stub
        instead of the full 303 -> /vod redirect that real browsers get, and
        the fresh device never becomes bound to the account. Navigating like
        a browser (no AJAX markers) forces the full server-side flow, and a
        following GET /profile?do=switchProfile binds the device/profile.
        """
        headers = self._default_headers()
        for key in ('X-Requested-With', 'Origin', 'Sec-Fetch-Site'):
            headers.pop(key, None)
        headers['Accept'] = (
            'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')
        headers['Referer'] = f'{self.base_url}/welcome/login'
        return headers

    def _navigate_follow(self, session, url, max_hops=8):
        """Follow a browser-like redirect chain (manual, no AJAX headers)."""
        headers = self._navigate_headers()
        resp = None
        seen = set()
        for _ in range(max_hops):
            if url in seen:
                break
            seen.add(url)
            resp = session.request('GET', url, headers=headers,
                                   allow_redirects=False, timeout=self.timeout)
            if resp is None:
                break
            location = (resp.headers or {}).get('Location')
            if resp.status_code in (301, 302, 303, 307, 308) and location:
                url = urllib.parse.urljoin(self.base_url, location)
                continue
            return resp
        return resp

    def _redirect_target(self, response):
        """Extract the redirect target from a response (Link header or JSON)."""
        location = (response.headers or {}).get('Location')
        if location:
            return urllib.parse.urljoin(self.base_url, location)
        body = response.text or ''
        if body.strip().startswith('{'):
            try:
                payload = json.loads(body)
                if isinstance(payload, dict) and payload.get('redirect'):
                    return urllib.parse.urljoin(self.base_url, payload['redirect'])
            except Exception:
                pass
        return None

    def login(self, email=None, password=None):
        if not email or not password:
            email = addon.getSetting('email')
            password = addon.getSetting('password')
            if not email or not password:
                self._log('Email or password not provided', xbmc.LOGWARNING)
                return False
        try:
            session = Session()
            session.headers.update(get_default_headers())
            headers = self._navigate_headers()
            login_page_url = f'{self.base_url}/welcome/login'

            response = session.request(
                'GET', login_page_url, headers=headers,
                allow_redirects=False, timeout=self.timeout)
            if not response or response.status_code >= 400:
                self._log('Failed to load login page', xbmc.LOGERROR)
                return False

            self._log(f'login: GET status={response.status_code}, '
                      f'len={len(response.content)}')

            csrf_token = form_action = None
            token_match = re.search(
                r'name=["\']_token_["\']\s+value=["\']([^"\']*)["\']',
                response.text or '', re.I)
            if token_match:
                csrf_token = token_match.group(1)
            do_match = re.search(
                r'name=["\']_do["\']\s+value=["\']([^"\']*)["\']',
                response.text or '', re.I)
            if do_match:
                form_action = do_match.group(1)

            if not csrf_token:
                self._log('Could not extract CSRF token', xbmc.LOGERROR)
                return False

            form_data = {
                'username': email,
                'password': password,
                '_token_': csrf_token,
                '_do': form_action or 'userLoginControl-signInForm-submit',
            }
            login_response = session.request(
                'POST', login_page_url, data=form_data, headers=headers,
                allow_redirects=False, timeout=self.timeout)

            self._log(f'login: POST status={login_response.status_code}, '
                      f'len={len(login_response.content)}')

            if login_response.status_code >= 400:
                self._log(f'Login failed: status {login_response.status_code}',
                          xbmc.LOGERROR)
                return False

            has_auth_cookies = bool(
                session.cookies.get('device_auth')
                or session.cookies.get('device_id'))
            self._log(f'login: auth_cookies={has_auth_cookies}')

            start_url = self._redirect_target(login_response)
            if not start_url:
                start_url = f'{self.base_url}/vod'

            final_response = self._navigate_follow(session, start_url)
            if final_response is None:
                self._log('login: redirect chain failed', xbmc.LOGERROR)
                return False

            body = final_response.text or ''
            self._log(f'login: landed on '
                      f'{urllib.parse.urlparse(final_response.url).path} '
                      f'len={len(body)} profile={"switchProfile" in body}')

            # After a fresh login the server forces the profile picker;
            # selecting the profile binds the device so /vod/play works.
            if 'switchProfile' in body:
                match = re.search(
                    r'href=["\']([^"\']*do=switchProfile[^"\']*)["\']',
                    body, re.I)
                if match:
                    switch_url = urllib.parse.urljoin(
                        self.base_url, match.group(1).replace('&amp;', '&'))
                    self._log('login: switching profile to bind device')
                    final_response = self._navigate_follow(session, switch_url)

            body = (final_response.text if final_response else '') or ''
            login_page_marker = bool(re.search(
                r'userLoginControl|signInForm', body[:3000], re.I))
            self._log(f'login: final status='
                      f'{final_response.status_code if final_response else "None"} '
                      f'len={len(body)} logged_in_page={login_page_marker}')

            if not has_auth_cookies or login_page_marker:
                self._log('Login form submitted but auth not confirmed',
                          xbmc.LOGWARNING)
                return False

            self._session_invalid = False
            self.session.cookies.clear()
            self.session_cookie_store.clear()
            for cookie in session.cookies:
                self.session.cookies.set(
                    cookie.name, cookie.value,
                    domain=cookie.domain or 'edisonline.sk',
                    path=cookie.path or '/', secure=cookie.secure)
                self.session_cookie_store[cookie.name] = cookie.value
            self._persist_session()
            self._log('Login successful - authenticated (profile bound)')
            return True

        except Exception as exc:
            self._log(f'Login error: {str(exc)}', xbmc.LOGERROR)
            return False
