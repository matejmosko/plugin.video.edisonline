#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plugin.video.edisonline - Main Entry Point
Kodi 22 video addon for Edisonline streaming with Widevine DRM support

Entry point that routes URL requests to appropriate handlers
"""

import sys
import xbmc
import xbmcaddon
from urllib.parse import parse_qs

# Add lib directory to path
import os
lib_path = os.path.join(os.path.dirname(__file__), 'lib')
if lib_path not in sys.path:
    sys.path.insert(0, lib_path)

try:
    from router import Router
except ImportError:
    # Fallback import
    import importlib.util
    spec = importlib.util.spec_from_file_location('router', os.path.join(lib_path, 'router.py'))
    router_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(router_module)
    Router = router_module.Router

# Plugin constants
ADDON_ID = 'plugin.video.edisonline'
addon = xbmcaddon.Addon(ADDON_ID)

def log(message, level=xbmc.LOGINFO):
    """Log message to Kodi debug log"""
    debug = addon.getSetting('debug_logging') == 'true'
    if debug:
        xbmc.log(f'[Edisonline] {message}', level)

def main():
    """
    Main entry point for plugin
    
    Kodi calls this plugin via URL with structure:
        plugin://plugin.video.edisonline/?action=...&param1=value1&param2=value2
    
    sys.argv structure:
        [0] = plugin://plugin.video.edisonline/
        [1] = handle (integer, plugin handle)
        [2] = ?action=...&param1=value1 (query string)
    """
    
    # Parse plugin URL
    base_url = sys.argv[0]
    handle = int(sys.argv[1])
    query_string = sys.argv[2] if len(sys.argv) > 2 else ''
    
    log(f'Plugin call: {base_url}?{query_string}')
    
    # Parse query parameters
    params = {}
    if query_string:
        # Strip all leading '?' if present (parse_qs does NOT do this and treats
        # it as part of the first key, e.g. '?action=x' -> {'?action': 'x'}).
        qs = query_string.lstrip('?')
        params = parse_qs(qs)
        # parse_qs returns lists, flatten to single values
        params = {k: v[0] if v else '' for k, v in params.items()}
    
    action = params.get('action', 'main')
    log(f'Action: {action}, Params: {params}')
    
    try:
        # Initialize router
        router = Router(handle, base_url)
        
        # Route to appropriate handler based on action
        if action == 'main':
            router.show_main_menu()
        
        elif action == 'movies':
            page = int(params.get('page', 1))
            genre = params.get('genre')
            router.show_movies(page=page, genre=genre)
        
        elif action == 'genres':
            router.show_genres()
        
        elif action == 'all_movies':
            page = int(params.get('page', 1))
            router.show_all_movies(page=page)
        
        elif action == 'recommendations':
            router.show_recommendations()
        
        elif action == 'search':
            router.search()
        
        elif action == 'play':
            movie_id = params.get('movie_id')
            if movie_id:
                router.play_movie(movie_id)
            else:
                log('Missing movie_id parameter', xbmc.LOGERROR)

        elif action == 'clear_cache':
            router.clear_metadata_cache()

        elif action == 'versions':
            # Handle versions/context menu action - show movie info or play
            movie_id = params.get('movie_id')
            if movie_id:
                router.play_movie(movie_id)
            else:
                router.show_main_menu()
        
        else:
            log(f'Unknown action: {action}', xbmc.LOGWARNING)
            router.show_main_menu()
    
    except Exception as e:
        log(f'Fatal error in main: {str(e)}', xbmc.LOGERROR)
        import traceback
        log(traceback.format_exc(), xbmc.LOGERROR)

if __name__ == '__main__':
    main()
