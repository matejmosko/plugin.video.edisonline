"""
Widevine DRM and inputstream.adaptive Configuration
Handles stream resolution, DRM license requests, and playback setup
"""

import sys
import os
import xbmc
import xbmcgui
from urllib.parse import quote

# Add lib directory to path
sys.path.insert(0, os.path.dirname(__file__))

try:
    import api
except ImportError:
    from lib import api

from _base import addon, show_notification


def _set_movie_info(info_tag, data):
    """Set movie metadata on an InfoTagVideo from a data dict.

    Each setter is individually wrapped in try/except so one failure
    (e.g. bad year value) doesn't prevent the rest from being applied.
    """
    for setter, key, convert in [
        (info_tag.setPlot, 'plot', None),
        (info_tag.setYear, 'year', lambda v: int(v)),
        (info_tag.setDuration, 'runtime_min', lambda v: int(v) * 60),
        (info_tag.setGenres, 'genres',
         lambda v: list(v) if isinstance(v, list) else [v]),
        (info_tag.setDirectors, 'director', lambda v: [v]),
        (info_tag.setCountries, 'country', lambda v: [v]),
        (info_tag.setTrailer, 'trailer_url', None),
    ]:
        val = data.get(key)
        if val:
            try:
                setter(convert(val) if convert else val)
            except Exception:
                pass
    if data.get('rating'):
        try:
            info_tag.setRating(float(data['rating']))
        except (TypeError, ValueError):
            pass
    if data.get('cast'):
        try:
            actors = [xbmc.Actor(name=a) for a in data['cast']]
            info_tag.setCast(actors)
        except Exception:
            pass


class DRMHandler:
    """Handle Widevine DRM and inputstream.adaptive configuration"""
    
    def __init__(self, api_instance=None):
        self.api = api_instance or api.EdisononlineAPI()
        self.debug = addon.getSetting('debug_logging') == 'true'
    
    def _log(self, message, level=xbmc.LOGINFO):
        """Log message to Kodi debug log"""
        if self.debug:
            xbmc.log(f'[Edisonline-DRM] {message}', level)
    
    def _show_notification(self, title, message, error=False):
        show_notification(title, message, error=error)

    def _show_buy_dialog(self, movie_title):
        """Show a modal dialog informing the user the title must be purchased."""
        try:
            ok = xbmcgui.Dialog().ok(
                addon.getLocalizedString(32306),
                f'{movie_title}\n\n'
                + addon.getLocalizedString(32307)
            )
            self._log(f'show_buy_dialog: user acknowledged purchase dialog ({ok})')
        except Exception as exc:
            self._log(f'show_buy_dialog: error showing dialog: {exc}', xbmc.LOGERROR)

    def get_stream_info(self, movie_id, movie_title, notify=True):
        """Fetch the real OTT stream manifest from the browser-signed flow."""
        self._log(f'Getting OTT stream info for {movie_title} (ID: {movie_id})')

        progress = xbmcgui.DialogProgress()
        progress.create(movie_title, addon.getLocalizedString(32305))

        try:
            stream_response = self.api.get_stream_url(movie_id)
            self._log(f'get_stream_info: stream_response={stream_response}', xbmc.LOGINFO)
            if not stream_response:
                progress.close()
                if notify:
                    self._show_notification(
                        addon.getLocalizedString(32304),
                        f'{movie_title} nie je dostupný',
                        error=True,
                    )
                self._log(f'Failed to resolve signed stream URL for {movie_id}')
                return None

            manifest_url = stream_response.get('manifest_url')
            license_url = stream_response.get('license_url')
            manifest_type = stream_response.get('manifest_type', 'hls')
            drm_system = stream_response.get('drm_system', 'widevine')
            requires_purchase = bool(stream_response.get('requires_purchase'))

            if not manifest_url:
                progress.close()
                if requires_purchase:
                    self._log(f'get_stream_info: {movie_title} requires purchase (paywall)', xbmc.LOGWARNING)
                    return {'requires_purchase': True, 'title': movie_title, 'movie_id': movie_id}
                if notify:
                    self._show_notification(
                        addon.getLocalizedString(32304),
                        'Manifest nie je dostupný',
                        error=True,
                    )
                return None

            self._log(f'Manifest URL: {manifest_url[:80]}...')
            self._log(f'DRM system: {drm_system}')
            if license_url:
                self._log(f'License URL: {license_url[:80]}...')
            else:
                self._log('No license URL - stream may be clear (no DRM)', xbmc.LOGINFO)

            license_type = ''
            if drm_system == 'widevine' and license_url:
                license_type = 'com.widevine.alpha'

            progress.close()
            stream_info = {
                'manifest_url': manifest_url,
                'license_url': license_url,
                'manifest_type': manifest_type,
                'license_type': license_type,
                'movie_id': movie_id,
                'title': movie_title,
            }
            return stream_info

        except Exception as exc:
            progress.close()
            if notify:
                self._show_notification(
                    addon.getLocalizedString(32302),
                    f'Chyba pri načítavaní: {str(exc)}',
                    error=True,
                )
            self._log(f'Exception in get_stream_info: {str(exc)}', xbmc.LOGERROR)
            return None
    
    def build_inputstream_properties(self, stream_info):
        """Build properties for inputstream.adaptive using the real HLS/Widevine flow."""
        self._log('Building inputstream.adaptive properties')

        properties = {
            'inputstream': 'inputstream.adaptive',
            'inputstream.adaptive.manifest_type': stream_info.get('manifest_type', 'hls'),
        }

        if stream_info.get('license_type') and stream_info.get('license_url'):
            properties['inputstream.adaptive.license_type'] = stream_info['license_type']
            license_key = self._build_license_key(
                stream_info['license_url'],
                stream_info.get('movie_id') or ''
            )
            if license_key:
                properties['inputstream.adaptive.license_key'] = license_key

        headers = f'User-Agent={quote("Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0")}&Referer={quote("https://edisonline.sk/")}'
        properties['inputstream.adaptive.stream_headers'] = headers

        # For clear HLS streams (no DRM), DON'T set license_type/license_key at all
        # Setting them to empty strings causes inputstream.adaptive to fail with
        # "ParseDrmOldProps: Cannot parse DRM configuration, unknown key system"
        # Only set buffer size to prevent buffering issues on clear streams
        if not stream_info.get('license_url'):
            # Set buffer size to 30 seconds to prevent buffering issues on clear streams
            properties['inputstream.adaptive.buffer_size'] = '30'

        max_bitrate = int(addon.getSetting('max_bitrate') or 0)
        if max_bitrate > 0:
            properties['inputstream.adaptive.max_bandwidth'] = max_bitrate * 1000

        self._log(f'Properties: {list(properties.keys())}')
        return properties
    
    def _build_license_key(self, license_url, movie_id):
        """
        Build Widevine license key string for inputstream.adaptive
        
        Format: {license_url}|{headers}|{data}|b64
        
        Args:
            license_url (str): Widevine license server URL
            movie_id (str): Movie ID for license request
        
        Returns:
            str: Formatted license key for inputstream.adaptive
        """
        try:
            # Build custom headers for license request
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                'Content-Type': 'application/octet-stream',
                'X-Movie-ID': movie_id,
            }
            
            # Convert headers dict to pipe-separated string
            header_str = '&'.join([f'{k}={quote(v)}' for k, v in headers.items() if v])
            
            # License key format: url|headers|data|b64
            # The {data} part will be replaced with actual license challenge at playback
            license_key = f'{license_url}|{header_str}||b64'
            
            self._log(f'License key built for movie {movie_id}')
            return license_key
        
        except Exception as e:
            self._log(f'Error building license key: {str(e)}', xbmc.LOGERROR)
            return None
    
    def resolve_stream(self, movie_id, movie_title, movie_data=None, notify=True):
        """
        Resolve stream and set it up for playback with xbmcplugin.setResolvedUrl()
        
        Args:
            movie_id (str): Movie ID
            movie_title (str): Movie title
            handle (int): Plugin handle from sys.argv[1]
            notify (bool): Show error notifications on failure. Pass False for
                a quiet probe attempt (e.g. router retries after a stale session).
        
        Returns:
            bool: True if resolved successfully
        """
        self._log(f'Resolving stream for {movie_title} (ID: {movie_id})')
        
        # Get stream info
        stream_info = self.get_stream_info(movie_id, movie_title, notify=notify)
        if not stream_info:
            self._log(f'resolve_stream: get_stream_info returned None for {movie_id}', xbmc.LOGERROR)
            if notify:
                self._show_notification(
                    addon.getLocalizedString(32302),
                    'Nepodarilo sa získať informácie o streame',
                    error=True
                )
            return None

        # Title is behind a paywall (purchasable but not playable under the
        # current subscription). Ask the user to buy it instead of failing.
        if stream_info.get('requires_purchase'):
            self._log(f'resolve_stream: {movie_title} requires purchase (paywall)', xbmc.LOGWARNING)
            self._show_buy_dialog(movie_title)
            return 'purchase_required'
        
        self._log(f'resolve_stream: stream_info={stream_info}', xbmc.LOGINFO)
        
        try:
            manifest_url = stream_info['manifest_url']
            license_url = stream_info.get('license_url')
            manifest_type = stream_info.get('manifest_type', 'hls')
            license_type = stream_info.get('license_type', '')
            
            headers_str = 'User-Agent=Mozilla%2F5.0%20(X11%3B%20Linux%20x86_64%3B%20rv%3A140.0)%20Gecko%2F20100101%20Firefox%2F140.0&Referer=https%3A%2F%2Fedisonline.sk%2F'
            if '|' not in manifest_url:
                manifest_url_with_headers = f'{manifest_url}|{headers_str}'
            else:
                manifest_url_with_headers = manifest_url

            # Build ListItem for playback
            list_item = xbmcgui.ListItem(path=manifest_url_with_headers)
            
            # Set inputstream.adaptive properties
            inputstream_props = self.build_inputstream_properties(stream_info)
            list_item.setProperty('inputstream', inputstream_props.get('inputstream', ''))
            list_item.setProperty('inputstream.adaptive.manifest_type', 
                                 inputstream_props.get('inputstream.adaptive.manifest_type', ''))
            if inputstream_props.get('inputstream.adaptive.license_type'):
                list_item.setProperty('inputstream.adaptive.license_type', 
                                     inputstream_props.get('inputstream.adaptive.license_type', ''))
                list_item.setProperty('inputstream.adaptive.license_key', 
                                     inputstream_props.get('inputstream.adaptive.license_key', ''))
            if inputstream_props.get('inputstream.adaptive.stream_headers'):
                list_item.setProperty('inputstream.adaptive.stream_headers', 
                                     inputstream_props.get('inputstream.adaptive.stream_headers', ''))
            
            # For clear HLS streams (no DRM), ensure proper configuration
            if not license_url:
                list_item.setProperty('inputstream.adaptive.license_type', '')
                list_item.setProperty('inputstream.adaptive.license_key', '')
                self._log('No license URL - configuring for clear HLS stream', xbmc.LOGINFO)

            # Set media info using modern InfoTagVideo API (Kodi 22+)
            info_tag = list_item.getVideoInfoTag()
            info_tag.setTitle(movie_title)
            info_tag.setMediaType('movie')
            if movie_data:
                _set_movie_info(info_tag, movie_data)
                self._apply_art_from_detail(list_item, movie_data)

            self._log(f'Stream resolved: {stream_info["manifest_url"][:50]}...')
            
            return list_item
        
        except Exception as e:
            self._show_notification(
                addon.getLocalizedString(32303),
                f'Chyba pri konfigurácii streamu: {str(e)}',
                error=True
            )
            self._log(f'Exception in resolve_stream: {str(e)}', xbmc.LOGERROR)
            return None
    
    def _apply_art_from_detail(self, list_item, movie_data):
        """Apply fanart/thumb/poster from detail data, tolerating a missing backdrop.

        Note: In Kodi 20+ xbmcgui.ListItem.getArt() REQUIRES a 'key' argument
        (it returns a single art path string), so we never call it bare here.

        Kodi uses 'thumb' for list thumbnails and 'poster' for the info dialog
        detail view, so we set both from poster_url.
        """
        try:
            thumb = movie_data.get('poster_url') or movie_data.get('thumb')
            fanart = movie_data.get('backdrop') or movie_data.get('fanart_url')
            art = {}
            if thumb:
                art['thumb'] = thumb
                art['poster'] = thumb
            if fanart:
                art['fanart'] = fanart
            if art:
                list_item.setArt(art)
        except Exception as exc:
            self._log(f'_apply_art_from_detail: {exc}', xbmc.LOGWARNING)

    def get_list_item_from_movie(self, movie_data):
        """
        Convert API movie data to Kodi ListItem with metadata
        
        Args:
            movie_data (dict): Movie data from API with keys:
                - id, title, plot, year, genres, duration, poster_url, fanart_url, rating
        
        Returns:
            xbmcgui.ListItem: Configured ListItem for display in Kodi
        """
        try:
            title = movie_data.get('title', 'Unknown')
            list_item = xbmcgui.ListItem(label=title)

            # Set media info using modern InfoTagVideo API (Kodi 22+)
            info_tag = list_item.getVideoInfoTag()
            info_tag.setTitle(title)
            info_tag.setMediaType('movie')
            _set_movie_info(info_tag, movie_data)

            # Art
            self._apply_art_from_detail(list_item, movie_data)

            self._log(f'ListItem created for {title}')
            return list_item

        except Exception as e:
            self._log(f'Error creating ListItem: {str(e)}', xbmc.LOGERROR)
            return None
