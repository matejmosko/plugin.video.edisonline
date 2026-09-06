"""Shared Kodi addon singleton, logging and notification helpers."""

import xbmc
import xbmcgui
import xbmcaddon

ADDON_ID = 'plugin.video.edisonline'

addon = xbmcaddon.Addon(ADDON_ID)


def log(message, level=xbmc.LOGINFO, tag='Edisonline', debug_setting=None):
    """Log to Kodi log when debug_logging is enabled."""
    if debug_setting is None:
        try:
            debug_setting = addon.getSetting('debug_logging') == 'true'
        except Exception:
            debug_setting = False
    if debug_setting:
        xbmc.log(f'[{tag}] {message}', level)


def show_notification(title, message, error=False, timeout=5000):
    """Show a Kodi notification."""
    icon = xbmcgui.NOTIFICATION_ERROR if error else xbmcgui.NOTIFICATION_INFO
    xbmcgui.Dialog().notification(title, message, icon, timeout)
