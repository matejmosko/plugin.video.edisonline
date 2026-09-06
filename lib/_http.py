"""Minimal stdlib HTTP transport used by the Edisonline API.

Replaces the requests library with a thin urllib wrapper that supports:
- cookie jars, redirect control, gzip/deflate decompression
"""

import gzip
import http.cookiejar
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib


def _decompress(headers, content):
    """Decode a body per its Content-Encoding so .text/.json see real bytes."""
    if headers is None:
        return content
    try:
        encoding = (headers.get('Content-Encoding') or '').strip().lower()
    except Exception:
        return content
    if not encoding or encoding in ('identity', '*'):
        return content
    try:
        if encoding in ('gzip', 'x-gzip'):
            return gzip.decompress(content)
        if encoding == 'deflate':
            try:
                return gzip.decompress(content)
            except Exception:
                return zlib.decompress(content)
        return content
    except Exception:
        return content


def build_multipart(fields):
    """Return (content_type, body_bytes) for a multipart/form-data POST."""
    import uuid
    boundary = '----geckoformboundary' + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(
            ('--' + boundary + '\r\n'
             'Content-Disposition: form-data; name="%s"\r\n\r\n%s\r\n')
            % (name, value)
        )
    parts.append('--' + boundary + '--\r\n')
    body = ''.join(parts).encode('utf-8')
    return 'multipart/form-data; boundary=' + boundary, body


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent urllib from auto-following redirects so we can inspect Location."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class CookieJar:
    """Thin stdlib-backed cookie jar mirroring the requests cookie API."""

    def __init__(self):
        self._jar = http.cookiejar.CookieJar()

    def get(self, name, default=None):
        for cookie in self._jar:
            if cookie.name == name and not cookie.value is None:
                return cookie.value
        return default

    def set(self, name, value, domain='', path='/', secure=None):
        cookie = http.cookiejar.Cookie(
            version=0,
            name=name,
            value=str(value),
            port=None,
            port_specified=False,
            domain=domain,
            domain_specified=bool(domain),
            domain_initial_dot=False,
            path=path,
            path_specified=bool(path),
            secure=bool(secure),
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={'HttpOnly': None},
            rfc2109=False,
        )
        self._jar.set_cookie(cookie)

    def clear(self):
        self._jar.clear()

    def __iter__(self):
        return iter(self._jar)

    def __len__(self):
        return len(self._jar)


class Response:
    """Minimal response wrapper with the requests-like read API."""

    def __init__(self, status, headers, content, url=''):
        self.status_code = status
        self.headers = headers
        self.content = content
        self.url = url

    @property
    def text(self):
        return self.content.decode('utf-8', errors='replace')

    def raise_for_status(self):
        if self.status_code >= 400:
            raise urllib.error.HTTPError(
                self.url, self.status_code, 'HTTP Error', self.headers, None)

    def json(self):
        return json.loads(self.text)


class Session:
    """Minimal stdlib (urllib) replacement for the requests features used here."""

    def __init__(self):
        self.headers = {}
        self.cookies = CookieJar()

    def _open(self, url, body=None, headers=None, method=None, timeout=None):
        request = urllib.request.Request(
            url, data=body, headers=headers or {}, method=method)
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies._jar),
            _NoRedirectHandler(),
        )
        try:
            response = opener.open(request, timeout=timeout)
            status = response.status
            resp_headers = response.headers
            content = response.read()
            response.close()
        except urllib.error.HTTPError as exc:
            status = exc.code
            resp_headers = exc.headers
            content = exc.read()
            exc.close()
        return Response(status, resp_headers,
                        _decompress(resp_headers, content), url)

    def request(self, method, url, params=None, data=None, headers=None,
                allow_redirects=True, timeout=None):
        if params:
            separator = '&' if '?' in url else '?'
            url = url + separator + urllib.parse.urlencode(params)

        req_headers = dict(self.headers)
        if headers:
            req_headers.update(headers)

        body = None
        if data is not None:
            if isinstance(data, dict):
                body = urllib.parse.urlencode(data).encode('utf-8')
            elif isinstance(data, str):
                body = data.encode('utf-8')
            else:
                body = data
            req_headers.setdefault('Content-Type',
                                   'application/x-www-form-urlencoded')

        response = self._open(url, body=body, headers=req_headers,
                              method=method.upper(), timeout=timeout)

        if allow_redirects and response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get('Location')
            if location:
                new_url = urllib.parse.urljoin(url, location)
                redirect_method = ('GET' if response.status_code in (301, 302, 303)
                                   else method.upper())
                return self.request(
                    redirect_method, new_url,
                    data=None if response.status_code in (301, 302, 303) else data,
                    headers=req_headers, allow_redirects=True, timeout=timeout)

        return response

    def get(self, url, **kwargs):
        return self.request('GET', url, **kwargs)

    def post(self, url, **kwargs):
        return self.request('POST', url, **kwargs)
