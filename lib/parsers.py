"""HTML and content parsing helpers for the Edisonline OTT flow.

All parsing functions accept a ``log`` callback
``log(message, level)`` so they remain decoupled from the API class.
"""

import json
import re

_HTML_AMP_RE = re.compile(r'&amp;', re.I)


def clean_stream_url(url):
    """Safely unescape a signed stream/license URL without corrupting it.

    Never use html.unescape() on a URL: it greedily converts entity prefixes
    like "&parallel-limit=3" -> "¶llel-limit=3".
    """
    if not url:
        return url
    url = _HTML_AMP_RE.sub('&', url)
    url = url.replace('&lt;', '<').replace('&gt;', '>')
    url = url.replace('&quot;', '"').replace('&#39;', "'")
    try:
        url.encode('ascii')
        return url
    except (UnicodeEncodeError, UnicodeDecodeError):
        import urllib.parse
        return urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%-_.~\\")


# Widevine license server for the moderntv OTT platform
WIDEVINE_LICENSE_URL = 'https://drm.srv.czcloud.i.mtvreg.com/license/prod/widevine/'
_WIDEVINE_KEYFORMAT = 'edef8ba9-79d6-4ace-a3c8-27dcd51d21ed'
_DRM_URL_MARKERS = ('drmProvider=', 'drmTypes=widevine', 'packager=', 'drm-system=widevine')


def extract_stream_url(text, log=None):
    """Extract the main movie stream URL (not trailer) from page text."""
    _log = log or (lambda m, **kw: None)
    patterns = [
        (r'https?://stream\.moderntv\.eu/stream\.m3u8\?[^\'"\s>]+', 'main stream'),
        (r'https?://[^\'"\s>]*stream[^\'"\s>]*\.m3u8\?[^\'"\s>]*(?:drm-system|widevine|license)[^\'"\s>]*', 'DRM stream'),
        (r'https?://[^\'"\s>]*moderntv[^\'"\s>]*\.m3u8\?[^\'"\s>]+', 'moderntv subdomain stream'),
    ]
    for pattern, desc in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            url = match.group(0)
            _log(f'extract_stream_url: found {desc}: {url[:80]}...')
            return clean_stream_url(url)

    # CDN stream — reject trailers (they have 'amp;' or 'vod-event-id=')
    match = re.search(
        r'https?://[^\'"\s>]*stream[^\'"\s>]*\.m3u8\?(?:(?!amp;|vod-event-id=)[^\'"\s>])+',
        text, re.I)
    if match:
        url = match.group(0)
        if 'vod-event-id=' not in url and 'amp;' not in url:
            _log(f'extract_stream_url: found CDN stream: {url[:80]}...')
            return clean_stream_url(url)

    # Fallback: any stream.m3u8 (might be trailer)
    match = re.search(r'https?://[^\'"\s>]*stream[^\'"\s>]*\.m3u8\?[^\'"\s>]+', text, re.I)
    if match:
        url = match.group(0)
        _log(f'extract_stream_url: fallback stream (may be trailer): {url[:80]}...')
        return clean_stream_url(url)

    return None


def extract_license_url(text):
    """Extract Widevine license URL from content detail response or HLS manifest."""
    patterns = [
        r'license[Uu]rl["\']?\s*[:=]\s*["\']([^"\']+)["\']',
        r'widevine[Ll]icense[Uu]rl["\']?\s*[:=]\s*["\']([^"\']+)["\']',
        r'https?://[^\'"\s>]*license[^\'"\s>]*',
        r'https?://[^\'"\s>]*widevine[^\'"\s>]*',
        r'data-license[Uu]rl=["\']([^"\']+)["\']',
        r'data-widevine[Ll]icense=["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            url = match.group(1) if match.groups() else match.group(0)
            return clean_stream_url(url)
    return None


def parse_catalog_page(html, log=None):
    """Parse catalog HTML into a list of movie dicts with category tags."""
    _log = log or (lambda m, **kw: None)
    if not html:
        _log('parse_catalog_page: empty html input')
        return []

    # 1. Build category-to-movie-ID mapping from strip segments
    cat_map = {}
    cat_label_map = {}
    try:
        cat_pattern = re.compile(
            r'data-spline-category="(tvTipsCategory:[^"]+)"', re.I)
        # Fallback: correct typo in attribute name
        if not cat_pattern.search(html):
            cat_pattern = re.compile(
                r'data-splide-category="(tvTipsCategory:[^"]+)"', re.I)
        cat_splits = list(cat_pattern.finditer(html))
        for idx, m in enumerate(cat_splits):
            slug = m.group(1)
            seg_start = m.end()
            seg_end = cat_splits[idx + 1].start() if idx + 1 < len(cat_splits) else len(html)
            segment = html[seg_start:seg_end]

            seg_before = html[max(0, m.start() - 10000):m.start()]
            label_m = re.search(r'<h2[^>]*>\s*([^<]+)\s*</h2>', seg_before, re.S)
            label = ' '.join(label_m.group(1).split()) if label_m else slug
            cat_label_map[slug] = label

            for mid in set(re.findall(r'vodEntry:(\d+)', segment)):
                cat_map.setdefault(mid, [])
                if label not in cat_map[mid]:
                    cat_map[mid].append(label)
        _log(f'parse_catalog_page: category mapping for {len(cat_map)} movies '
             f'from {len(cat_splits)} strips')
    except Exception as exc:
        _log(f'parse_catalog_page: category extraction failed: {exc}')

    # 2. Parse individual movie cards
    movies = []
    cards = re.findall(
        r'<img src="([^"]+)" alt="([^"]*)"[^>]*class="[^"]*tw-h-full[^"]*"[^>]*>(?:(?!</li>).)*?'
        r'<a href="/content/detail/[^"]*vodEntry:(\d+)"',
        html,
        re.S | re.I,
    )
    _log(f'parse_catalog_page: html={len(html)} bytes, {len(cards)} cards')
    for poster, title, mid in cards:
        title = ' '.join(title.split())
        if not title:
            continue
        entry = {
            'id': mid,
            'title': title,
            'poster_url': clean_stream_url(poster),
        }
        categories = cat_map.get(mid)
        if categories:
            entry['categories'] = categories
        movies.append(entry)

    if not movies:
        slugs = re.findall(
            r'<a href="/content/detail/[^"]*"(?:(?!</a>).)*?vodEntry:(\d+)[^>]*>',
            html, re.S | re.I)
        for mid in slugs:
            entry = {'id': mid, 'title': 'Edisonline ' + mid, 'poster_url': None}
            categories = cat_map.get(mid)
            if categories:
                entry['categories'] = categories
            movies.append(entry)

    return movies


def parse_search_page(html, log=None):
    """Parse search result HTML into a list of movie dicts."""
    _log = log or (lambda m, **kw: None)
    if not html:
        _log('parse_search_page: empty html input')
        return []
    movies = []
    seen = set()
    cards = re.findall(
        r'<a href="/content/detail/[^"]*-online-vodEntry:(\d+)"[^>]*>\s*'
        r'<img src="([^"]*)"[^>]*>.*?</a>.*?x-marquee-speed[^>]*>\s*'
        r'([^<]+?)\s*</div>',
        html,
        re.S | re.I,
    )
    _log(f'parse_search_page: html={len(html)} bytes, {len(cards)} cards')
    for mid, poster, title in cards:
        if mid in seen:
            continue
        seen.add(mid)
        title = ' '.join(title.split())
        if not title:
            continue
        movies.append({
            'id': mid,
            'title': title,
            'poster_url': clean_stream_url(poster),
        })
    if not movies:
        slugs = re.findall(
            r'<a href="/content/detail/[^"]*-online-vodEntry:(\d+)"',
            html, re.S | re.I)
        for mid in slugs:
            if mid not in seen:
                seen.add(mid)
                movies.append({'id': mid, 'title': 'Edisonline ' + mid, 'poster_url': None})
    return movies


def parse_modal_metadata(modal_html, log=None):
    """Extract rich movie metadata from the detail modal HTML."""
    _log = log or (lambda m, **kw: None)

    def _clean(text):
        return re.sub(r'\s+', ' ', text).strip()

    meta = {
        'title': None, 'year': None, 'country': None, 'genres': [],
        'plot': None, 'director': None, 'cast': [], 'rating': None,
        'runtime_min': None, 'quality': None, 'mpaa': None,
    }

    m = re.search(r'<p\s+id="content-full-description"[^>]*>(.*?)</p>',
                  modal_html, re.S | re.I)
    if m:
        meta['plot'] = _clean(re.sub(r'<[^>]+>', ' ', m.group(1)))

    m = re.search(r'<span>\s*(\d{4})\s*</span>', modal_html)
    if m:
        meta['year'] = int(m.group(1))

    chips = re.findall(
        r'<span class="[^"]*tw-text-center[^"]*">\s*(.*?)\s*</span>',
        modal_html, re.S | re.I)
    country = None
    genres = []
    for chip in chips:
        text = _clean(re.sub(r'<[^>]+>', ' ', chip))
        if not text:
            continue
        if '/' in text:
            genres = [' '.join(g.split()) for g in text.split('/') if g.strip()]
        elif country is None and text and not re.match(r'^[\d\s]+$', text):
            country = text
    meta['country'] = country
    meta['genres'] = genres

    m = re.search(r'Vek\s*(\d{1,2})\+', modal_html)
    if m:
        meta['mpaa'] = m.group(1) + '+'

    m = re.search(r'Kvalita\s+obrazu:.*?<[^>]*>([^<]+)</', modal_html, re.S | re.I)
    if m:
        meta['quality'] = _clean(m.group(1))

    m = re.search(r'(\d+)\s*minút', modal_html)
    if m:
        meta['runtime_min'] = int(m.group(1))

    m = re.search(r'Tvorcovia</h[1-6]>\s*<[^>]*>\s*(.*?)</div>', modal_html, re.S | re.I)
    if m:
        d = _clean(re.sub(r'<[^>]+>', ' ', m.group(1)))
        if d:
            meta['director'] = d

    m = re.search(r'Hrajú</h[1-6]>(.*?)(?:<h[1-6]|$)', modal_html, re.S | re.I)
    if m:
        cast_names = re.findall(
            r'<a href="/catalog/category/[^"]*"[^>]*>(.*?)</a>',
            m.group(1), re.S | re.I)
        cast = []
        for c in cast_names:
            name = _clean(c)
            if name and name not in cast:
                cast.append(name)
        meta['cast'] = cast

    _log(f'parse_modal_metadata: title={meta["title"]} year={meta["year"]} '
         f'genres={meta["genres"]} cast={meta["cast"][:3]}')
    return meta
