"""
URL Router and Navigation Handler
Routes plugin URLs to appropriate functions and manages navigation flow
"""

import sys
import os
import xbmc
import xbmcgui
import xbmcplugin
from urllib.parse import urlencode

# Add lib directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'lib'))

try:
    import api
    import drm
except ImportError:
    from lib import api, drm

from _base import addon, log as _log_base, show_notification


class Router:
    """Handle URL routing and navigation"""

    def __init__(self, handle, base_url):
        """
        Initialize router

        Args:
            handle (int): Plugin handle from sys.argv[1]
            base_url (str): Plugin base URL from sys.argv[0]
        """
        self.handle = handle
        self.base_url = base_url
        self.api = api.EdisononlineAPI()
        self.drm = drm.DRMHandler(self.api)
        self.debug = addon.getSetting('debug_logging') == 'true'

    def _log(self, message, level=xbmc.LOGINFO):
        """Log message"""
        if self.debug:
            xbmc.log(f'[Edisonline-Router] {message}', level)

    def _show_notification(self, title, message, error=False):
        """Show notification"""
        show_notification(title, message, error=error)

    def _end_directory(self, succeeded=True):
        """End the container cleanly.

        Call with succeeded=False on error/empty-result exits so skin/widget
        containers see a consistent result instead of a dangling listdir
        (breaks the home/back stack order).
        """
        try:
            xbmcplugin.endOfDirectory(self.handle, succeeded=succeeded)
        except Exception as exc:
            self._log(f'_end_directory: {exc}', xbmc.LOGWARNING)

    def _build_url(self, **kwargs):
        """Build plugin URL with query parameters"""
        query = urlencode(kwargs)
        base = self.base_url.rstrip('?')
        return f'{base}?{query}'

    # Main Menu
    def show_main_menu(self):
        """Display main menu for the browser-OTT flow."""
        self._log('Showing main menu')

        self._require_session()

        menu_items = [
            {
                'label': addon.getLocalizedString(32200),  # Movies
                'icon': 'DefaultMovies.png',
                'url': self._build_url(action='movies')
            },
            {
                'label': addon.getLocalizedString(32201),  # Home/Recommendations
                'icon': 'DefaultFavourites.png',
                'url': self._build_url(action='recommendations')
            },
            {
                'label': addon.getLocalizedString(32202),  # Genres
                'icon': 'DefaultGenre.png',
                'url': self._build_url(action='genres')
            },
            {
                'label': addon.getLocalizedString(32204),  # All Movies (no metadata)
                'icon': 'DefaultMovies.png',
                'url': self._build_url(action='all_movies')
            },
            {
                'label': addon.getLocalizedString(32203),  # Search
                'icon': 'DefaultSearch.png',
                'url': self._build_url(action='search')
            },
        ]

        self._add_menu_items(menu_items, folder=True)
        xbmcplugin.endOfDirectory(self.handle)

    def show_movies(self, page=1, genre=None):
        """Display movies list using the browser session flow."""
        self._log(f'Showing movies (page {page}, genre: {genre})')

        if not self._require_session():
            self._end_directory(False)
            return

        # Show loading dialog
        progress = xbmcgui.DialogProgress()
        progress.create(addon.getLocalizedString(32305))  # Loading...

        try:
            # Fetch movies from API
            movies_data = self.api.get_movies(page=page, limit=20, genre=genre)
            progress.close()

            if not movies_data:
                self._show_notification(
                    addon.getLocalizedString(32302),  # Network Error
                    'Chyba pri načítavaní filmov',
                    error=True
                )
                self._end_directory(False)
                return

            movies = movies_data.get('movies', [])
            total_pages = movies_data.get('total_pages', 1)

            # Synchronously preload detail metadata for the first visible
            # items (default 10).  Already-cached movies are skipped so this
            # is instant on revisit.  Movies beyond the visible viewport are
            # fetched on-demand when the user clicks to play them.
            self.api.preload_visible_details(movies, count=20)

            # Enrich each movie with cached detail metadata (plot, cast,
            # year, genres, director, trailer, backdrop).
            movies = self._enrich_movies(movies)

            # Add movies to list
            for movie in movies:
                list_item = self.drm.get_list_item_from_movie(movie)
                if list_item:
                    # Ensure Kodi treats this as a playable item
                    list_item.setProperty('IsPlayable', 'true')
                    url = self._build_url(action='play', movie_id=movie.get('id'))
                    xbmcplugin.addDirectoryItem(
                        self.handle,
                        url,
                        list_item,
                        isFolder=False
                    )

            # Add pagination if needed
            if page < total_pages:
                list_item = xbmcgui.ListItem(label='[Ďalšia strana]')  # Next Page
                page_url_kwargs = {'action': 'movies', 'page': page + 1}
                if genre:
                    page_url_kwargs['genre'] = genre
                url = self._build_url(**page_url_kwargs)
                xbmcplugin.addDirectoryItem(self.handle, url, list_item, isFolder=True)

            # Allow standard movie views (Posters etc.)
            xbmcplugin.setContent(self.handle, 'movies')
            xbmcplugin.endOfDirectory(self.handle)

        except Exception as e:
            progress.close()
            self._show_notification(
                addon.getLocalizedString(32302),
                f'Chyba: {str(e)}',
                error=True
            )
            self._log(f'Exception in show_movies: {str(e)}', xbmc.LOGERROR)
            self._end_directory(False)

    def show_all_movies(self, page=1):
        """Display a lightweight catalog of ALL movies, paginated by 100.

        This list performs NO metadata scraping: movie details are only merged
        from the persistent per-movie cache when they are already available
        (e.g. a title that was watched or previously browsed).  Uncached titles
        show just their title and poster.  The container content type is set to
        'movies' so Kodi offers its usual views (Posters, etc.) for browsing.
        """
        self._log(f'Showing all movies (page {page})')

        if not self._require_session():
            self._end_directory(False)
            return

        progress = xbmcgui.DialogProgress()
        progress.create(addon.getLocalizedString(32305))  # Loading...

        try:
            movies_data = self.api.get_movies(page=page, limit=100)
            progress.close()

            if not movies_data:
                self._show_notification(
                    addon.getLocalizedString(32302),
                    'Chyba pri načítavaní filmov',
                    error=True
                )
                self._end_directory(False)
                return

            movies = movies_data.get('movies', [])
            total_pages = movies_data.get('total_pages', 1)

            # Merge only already-cached details (never scrapes the network).
            movies = self._enrich_movies(movies)

            # Allow standard movie views (Posters etc.)
            xbmcplugin.setContent(self.handle, 'movies')

            for movie in movies:
                list_item = self.drm.get_list_item_from_movie(movie)
                if list_item:
                    list_item.setProperty('IsPlayable', 'true')
                    url = self._build_url(action='play', movie_id=movie.get('id'))
                    xbmcplugin.addDirectoryItem(
                        self.handle, url, list_item, isFolder=False
                    )

            if page < total_pages:
                list_item = xbmcgui.ListItem(label='[Ďalšia strana]')  # Next Page
                url = self._build_url(action='all_movies', page=page + 1)
                xbmcplugin.addDirectoryItem(self.handle, url, list_item, isFolder=True)

            xbmcplugin.endOfDirectory(self.handle)

        except Exception as e:
            progress.close()
            self._show_notification(
                addon.getLocalizedString(32302),
                f'Chyba: {str(e)}',
                error=True
            )
            self._log(f'Exception in show_all_movies: {str(e)}', xbmc.LOGERROR)
            self._end_directory(False)

    def show_genres(self):
        """Display genres list."""
        self._log('Showing genres')

        if not self._require_session():
            self._end_directory(False)
            return

        try:
            genres_data = self.api.get_genres()

            if not genres_data:
                self._show_notification(
                    addon.getLocalizedString(32302),
                    'Chyba pri načítavaní žánrov',
                    error=True
                )
                self._end_directory(False)
                return

            genres = genres_data.get('genres', [])

            for genre in genres:
                list_item = xbmcgui.ListItem(label=genre.get('name', 'Unknown'))
                url = self._build_url(action='movies', genre=genre.get('id'))
                xbmcplugin.addDirectoryItem(self.handle, url, list_item, isFolder=True)

            xbmcplugin.endOfDirectory(self.handle)

        except Exception as e:
            self._show_notification(
                addon.getLocalizedString(32302),
                f'Chyba: {str(e)}',
                error=True
            )
            self._log(f'Exception in show_genres: {str(e)}', xbmc.LOGERROR)
            self._end_directory(False)

    def show_recommendations(self):
        """Display recommended movies."""
        self._log('Showing recommendations')

        if not self._require_session():
            self._end_directory(False)
            return

        try:
            movies_data = self.api.get_recommendations()

            if not movies_data:
                self._show_notification(
                    addon.getLocalizedString(32302),
                    'Chyba pri načítavaní odporúčaní',
                    error=True
                )
                self._end_directory(False)
                return

            movies = movies_data.get('recommendations', [])
            self.api.preload_visible_details(movies, count=20)
            movies = self._enrich_movies(movies)

            for movie in movies:
                list_item = self.drm.get_list_item_from_movie(movie)
                if list_item:
                    list_item.setProperty('IsPlayable', 'true')
                    url = self._build_url(action='play', movie_id=movie.get('id'))
                    xbmcplugin.addDirectoryItem(
                        self.handle,
                        url,
                        list_item,
                        isFolder=False
                    )

            # Allow standard movie views (Posters etc.)
            xbmcplugin.setContent(self.handle, 'movies')
            xbmcplugin.endOfDirectory(self.handle)

        except Exception as e:
            self._show_notification(
                addon.getLocalizedString(32302),
                f'Chyba: {str(e)}',
                error=True
            )
            self._log(f'Exception in show_recommendations: {str(e)}', xbmc.LOGERROR)
            self._end_directory(False)

    def search(self):
        """Handle search."""
        self._log('Search initiated')

        if not self._require_session():
            self._end_directory(False)
            return

        # Prompt for the search query (xbmcgui.Keyboard was removed in Kodi 22)
        query = xbmcgui.Dialog().input(
            'Vyhľadať',
            type=xbmcgui.INPUT_ALPHANUM
        )
        if not query:
            self._end_directory(False)
            return

        try:
            movies_data = self.api.search(query)

            if not movies_data:
                self._show_notification(
                    addon.getLocalizedString(32302),
                    'Žiadne výsledky',
                    error=False
                )
                self._end_directory(False)
                return

            movies = movies_data.get('results', [])
            self.api.preload_visible_details(movies, count=20)
            movies = self._enrich_movies(movies)

            for movie in movies:
                list_item = self.drm.get_list_item_from_movie(movie)
                if list_item:
                    list_item.setProperty('IsPlayable', 'true')
                    url = self._build_url(action='play', movie_id=movie.get('id'))
                    xbmcplugin.addDirectoryItem(
                        self.handle,
                        url,
                        list_item,
                        isFolder=False
                    )

            # Allow standard movie views (Posters etc.)
            xbmcplugin.setContent(self.handle, 'movies')
            xbmcplugin.endOfDirectory(self.handle)

        except Exception as e:
            self._show_notification(
                addon.getLocalizedString(32302),
                f'Chyba pri vyhľadávaní: {str(e)}',
                error=True
            )
            self._log(f'Exception in search: {str(e)}', xbmc.LOGERROR)
            self._end_directory(False)

    def play_movie(self, movie_id):
        """Resolve and play movie using the real browser-OTT contract."""
        self._log(f'Playing movie {movie_id}')

        if not self._require_session():
            self._end_directory(False)
            return

        try:
            # Get movie detail for title (also caches it for the list views)
            movie_data = self.api.get_movie_detail(movie_id)
            if not movie_data:
                movie_data = {'title': 'Video'}

            movie_title = movie_data.get('title', 'Video')
            self._log(f'play_movie: resolved title={movie_title}', xbmc.LOGINFO)

            # Resolve stream. If the stored session is stale the server
            # redirects playback to /profile (login); detect that, re-login
            # automatically with the saved credentials and retry once.
            resolved = self.drm.resolve_stream(
                movie_id, movie_title, movie_data, notify=False)
            if resolved == 'purchase_required':
                # Buy dialog already shown; just close playback cleanly.
                xbmcplugin.setResolvedUrl(self.handle, False, xbmcgui.ListItem())
                return
            if not resolved and self.api.needs_relogin():
                self._log('play_movie: stale session detected; re-login and '
                          'retry stream resolution', xbmc.LOGWARNING)
                self.api.invalidate_session()
                if self._require_session():
                    resolved = self.drm.resolve_stream(
                        movie_id, movie_title, movie_data)
                    if resolved == 'purchase_required':
                        xbmcplugin.setResolvedUrl(
                            self.handle, False, xbmcgui.ListItem())
                        return
                    if resolved:
                        xbmcplugin.setResolvedUrl(self.handle, True, resolved)
                        self._log(f'Playback started for {movie_title} '
                                  f'(after re-login)')
                        return
            if not resolved:
                self._log(f'play_movie: resolve_stream returned None', xbmc.LOGERROR)
                self._show_notification(
                    addon.getLocalizedString(32302),
                    'Nepodarilo sa rozlíšiť stream pre prehrávanie',
                    error=True
                )
                return

            # Set resolved URL for playback
            xbmcplugin.setResolvedUrl(self.handle, True, resolved)
            self._log(f'Playback started for {movie_title}')

        except Exception as e:
            xbmcplugin.setResolvedUrl(self.handle, False, xbmcgui.ListItem())
            self._show_notification(
                addon.getLocalizedString(32302),
                f'Chyba pri prehrávaní: {str(e)}',
                error=True
            )
            self._log(f'Exception in play_movie: {str(e)}', xbmc.LOGERROR)

    # Helper methods

    def _enrich_movies(self, movies):
        """Merge already-cached detail metadata into list items (no network).

        Details are cached when a movie is played (router.play_movie -> get_movie_detail)
        or prefetched in the background. Browsing a catalog page therefore renders
        instantly and only re-uses cached data; never-played movies show their
        catalog fields (title/poster/categories) until details are available.
        """
        if not movies:
            return movies or []
        enriched = []
        for movie in movies:
            mid = movie.get('id')
            detail = mid and self.api.get_cached_detail(mid)
            if detail:
                merged = dict(movie)
                for key in ('plot', 'year', 'director', 'cast',
                            'country', 'runtime_min', 'backdrop', 'trailer_url'):
                    if detail.get(key):
                        merged[key] = detail[key]
                # Merge detail genres with catalog categories (detail takes priority)
                detail_genres = detail.get('genres')
                catalog_cats = movie.get('categories')
                if detail_genres:
                    merged['genres'] = detail_genres
                elif catalog_cats:
                    merged['genres'] = catalog_cats
                movie = merged
            elif movie.get('categories') and not movie.get('genres'):
                # No cached detail yet — use catalog categories as genres
                movie = dict(movie)
                movie['genres'] = movie['categories']
            enriched.append(movie)
        return enriched

    def clear_metadata_cache(self):
        """Clear all cached movie metadata (in-memory + on-disk)."""
        self.api._clear_detail_cache()
        xbmcgui.Dialog().notification(
            addon.getLocalizedString(32003),  # Metadata
            addon.getLocalizedString(32011),  # Clear metadata cache
            xbmcgui.NOTIFICATION_INFO,
            3000,
        )

    def _require_session(self):
        """Ensure a usable OTT browser session exists, logging in if needed.

        Flow: if cookies are already present (manually pasted or previously
        captured) use them. Otherwise try saved email/password from settings,
        and finally prompt the user. This is how Kodi acquires the session
        itself, which matters on devices (e.g. LibreELEC) where the browser
        cookie-export flow is not an option.
        """
        ready = self.api.ensure_session_ready()
        self._log(f'_require_session: ready={ready} cookies={[c.name for c in self.api.session.cookies][:8]} store={list(self.api.session_cookie_store.keys())[:8]}')
        if ready:
            return True

        self._log('No active session; attempting automatic login')

        email = addon.getSetting('email')
        password = addon.getSetting('password')

        if not (email and password):
            credentials = self._prompt_credentials()
            if not credentials:
                self._show_notification(
                    'Session required',
                    'Enter your Edisonline email and password to sign in.',
                    error=True,
                )
                return False
            email, password = credentials

        if self.api.login(email, password):
            addon.setSetting('email', email)
            if addon.getSetting('remember_credentials') == 'true':
                addon.setSetting('password', password)
            self._show_notification(
                'Úspech',  # Success
                'Prihlásenie úspešné',  # Login successful
                error=False
            )
            self._log('Login succeeded, session established')
            return True

        self._show_notification(
            addon.getLocalizedString(32300),  # Authentication Failed
            addon.getLocalizedString(32301),  # Invalid Email or Password
            error=True
        )
        return False

    def _prompt_credentials(self):
        """Ask the user for email/password, returning (email, password) or None."""
        dialog = xbmcgui.Dialog()

        email = dialog.input(
            addon.getLocalizedString(32100),  # Email
            type=xbmcgui.INPUT_ALPHANUM,
            defaultt=addon.getSetting('email') or ''
        )
        if not email:
            return None

        password = dialog.input(
            addon.getLocalizedString(32101),  # Password
            type=xbmcgui.INPUT_ALPHANUM,
            option=xbmcgui.ALPHANUM_HIDE_INPUT
        )
        if not password:
            return None

        return email, password

    def _add_menu_items(self, items, folder=True):
        """Add multiple items to directory"""
        for item in items:
            list_item = xbmcgui.ListItem(label=item['label'])

            if item.get('icon'):
                list_item.setArt({'icon': item['icon'], 'thumb': item['icon']})

            xbmcplugin.addDirectoryItem(
                self.handle,
                item['url'],
                list_item,
                isFolder=folder
            )
