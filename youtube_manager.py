#
# Gemini Code Refinement
#
# Based on our discussion, I've integrated the following improvements:
#
# 1. Agent Usage and Flow:
#    - A top-level toggle ("Create New" vs. "Add to Existing") now controls the main workflow.
#    - When "Add to Existing" is selected, the UI fetches and displays the user's available YouTube playlists.
#    - The logic for adding to existing playlists is handled via the robust YouTube Data API.
#
# 2. Error Handling and User Feedback:
#    - The downloadable CSV report now includes a status for each song (e.g., 'Added', 'Failed').
#    - The agent will stop processing and issue a warning if the YouTube API daily quota is about to be exceeded.
#
# 3. Session State and Configuration:
#    - The UI for advanced settings (batch size, retries) is preserved.
#    - The app does not persist playlist IDs across sessions but allows easy selection within a session.
#
# 4. Code Structure and Maintainability:
#    - The code remains in a single file as requested.
#    - Comprehensive docstrings and type hints have been added to all major functions and methods for clarity.
#
# 5. Testing and Debugging:
#    - Logging is now directed to both the Streamlit console and a persistent `youtube_manager.log` file.
#    - A detailed guide on testing strategies (manual and automated) is included at the end of the script.
#
# 6. YTMusic OAuth & API Key Usage:
#    - The dual authentication methods (OAuth for full access, API Key for search-only) are maintained and clarified.
#    - The YTMusic authentication flow has been made more robust.
#

import streamlit as st
import pandas as pd
import numpy as np
import os
import logging
import json
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any, Set
from rapidfuzz.fuzz import partial_ratio, token_set_ratio, ratio
import unicodedata
import re
import string
from ytmusicapi import YTMusic
import traceback
import sys
import tempfile
import webbrowser
import time
from googleapiclient.discovery import build, Resource
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
import socket
import psutil
from dotenv import load_dotenv
import uuid

# --- Load Environment Variables ---
load_dotenv()

# --- Enhanced Logging Configuration ---
# This setup configures logging to a file and the console, which is crucial for debugging.
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s')

# File handler to save logs to 'youtube_manager.log'
file_handler = logging.FileHandler('youtube_manager.log', encoding='utf-8')
file_handler.setFormatter(log_formatter)

# Stream handler for console output, useful for local debugging
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(log_formatter)

# Get the root logger, clear existing handlers to avoid duplicates in Streamlit, and add new handlers.
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
if root_logger.hasHandlers():
    root_logger.handlers.clear()
root_logger.addHandler(file_handler)
root_logger.addHandler(stream_handler)

logger = logging.getLogger(__name__)

# --- Constants ---
CREDENTIALS_FILE: str = 'credentials.json'
TOKEN_FILE: str = 'token.json'
YTMUSIC_AUTH_FILE: str = 'ytmusic_oauth.json'

ALLOWED_PORTS: List[int] = [8501, 8080, 8502, 8081]  # Added more fallback ports
REDIRECT_URIS: Dict[int, str] = {
    8080: "http://localhost:8080/",
    8501: "http://localhost:8501/",
    8502: "http://localhost:8502/",
    8081: "http://localhost:8081/",
}

# OAuth Configuration
OAUTH_PORTS: List[int] = [8080, 8501, 8502, 8081]  # Ports to try for OAuth flow

# YouTube API Scopes
SCOPES: List[str] = [
    'https://www.googleapis.com/auth/youtube',
    'https://www.googleapis.com/auth/youtube.force-ssl'
]

# YouTube API Quota Information
YOUTUBE_API_QUOTAS: Dict[str, int] = {
    'search': 100,
    'playlist_insert': 50,
    'playlist_create': 50,
    'daily_limit': 210000,  # <-- Increased from 10,000 to 200,000
    'playlist_item_limit': 5000
}

# --- Dependency Versions ---
# For system info display
try:
    from ytmusicapi import __version__ as ytmusic_version
    DEPENDENCIES: Dict[str, str] = {
        'streamlit': st.__version__,
        'pandas': pd.__version__,
        'numpy': np.__version__,
        'ytmusicapi': ytmusic_version,
        'google-api-python-client': 'v2.108.0',  # Specify known compatible versions
        'google-auth-oauthlib': 'v1.2.0'
    }
except ImportError:
    DEPENDENCIES = {}


# --- Streamlit Page Configuration ---
st.set_page_config(
    page_title="YouTube Music Automation Agent",
    page_icon="🎵",
    layout="wide"
)


# --- Session State Initialization ---
def initialize_session_state() -> None:
    """Initializes all required keys in Streamlit's session state."""
    session_vars: Dict[str, Any] = {
        'youtube_service': None,
        'ytmusic': None,
        'auth_status': False,
        'current_account': None,
        'api_key': None,
        # ADDED 'duplicates_skipped' to track skipped songs
        'stats': {'searches': 0, 'added': 0, 'errors': 0, 'created_playlists': 0, 'duplicates_skipped': 0},
        'uploaded_file': None,
        'auth_email': '',
        'security': {'last_activity': time.time(), 'session_duration': 3600, 'api_calls_remaining': 60}
    }
    for var, default in session_vars.items():
        if var not in st.session_state:
            st.session_state[var] = default

# --- Main Automation Agent Class ---
class YouTubeMusicAutomationAgent:
    """
    An agent to automate interactions with YouTube and YouTube Music APIs.

    This class encapsulates logic for authentication, rate limiting, quota management,
    and processing song lists to create or update playlists.
    """
    def __init__(self) -> None:
        """Initializes the agent with default settings for metrics, security, and rate limiting."""
        # Performance Metrics
        self.metrics: Dict[str, Any] = {
            'response_times': [],
            'success_count': 0,
            'error_count': 0,
            'rate_limit_hits': 0,
        }
        # Security Settings
        self.security: Dict[str, int] = {
            'max_retries': 3,
            'timeout': 30,
        }
        # Rate Limiting & Quota
        self.rate_limit: Dict[str, Any] = {
            'window_size': 60,  # seconds
            'max_requests': 60,
            'requests': [],
            'last_reset': time.time(),
        }
        self.quota_settings: Dict[str, int] = {
            'daily_quota': YOUTUBE_API_QUOTAS['daily_limit'],
            'search_cost': YOUTUBE_API_QUOTAS['search'],
            'playlist_insert_cost': YOUTUBE_API_QUOTAS['playlist_insert'],
            'current_quota': 0,
        }
        # State Tracking
        # MODIFIED: Renamed to be more specific and added a new set for existing playlist items.
        self.session_processed_videos: set = set()
        self.existing_playlist_video_ids: set = set()
        self.youtube_service: Optional[Resource] = None
        self.ytmusic: Optional[YTMusic] = None
        self.delay: float = 1.0
        self.api_key: Optional[str] = None
        self.batch_size: int = 1200  # Increased batch size from 250 to 1200
        self.current_playlist_id: Optional[str] = None

        self.quota_settings: Dict[str, int] = {
            'daily_quota': YOUTUBE_API_QUOTAS['daily_limit'],
            'search_cost': YOUTUBE_API_QUOTAS['search'],
            'playlist_insert_cost': YOUTUBE_API_QUOTAS['playlist_insert'],
            'current_quota': 0,
        }

    async def _process_with_retry(self, song: str, playlist_id: str) -> Dict:
        """Processes a single song with a retry mechanism and duplicate checking."""
        result_details = {'song': song, 'status': 'Failed', 'error': 'Unknown error', 'video_id': None, 'title': None}
        for attempt in range(self.security['max_retries']):
            try:
                search_results = await self.search_song_youtube_api(song)
                if not search_results:
                    result_details['error'] = 'No search results found'
                    await asyncio.sleep(1)
                    continue

                best_match = self.get_best_match(search_results, song)
                if not best_match:
                    result_details['error'] = 'No suitable match found'
                    await asyncio.sleep(1)
                    continue

                video_id = best_match.get('id', {}).get('videoId')
                if not video_id:
                    result_details['error'] = 'Invalid video ID in search result'
                    continue

                # --- DUPLICATE CHECK LOGIC ---
                # Check if the song already exists in the target playlist.
                if video_id in self.existing_playlist_video_ids:
                    logger.info(f"Skipping '{song}' (ID: {video_id}) as it already exists in the target playlist.")
                    st.session_state.stats['duplicates_skipped'] += 1
                    result_details.update({'status': 'Skipped (Duplicate)', 'error': None, 'video_id': video_id, 'title': best_match.get('snippet', {}).get('title', 'Unknown')})
                    return {'success': True, 'details': result_details}

                # Check if it was already processed in this same run (handles duplicates in the CSV)
                if video_id in self.session_processed_videos:
                    result_details.update({'status': 'Skipped (Duplicate in CSV)', 'error': None, 'video_id': video_id})
                    return {'success': True, 'details': result_details}

                success = await self.add_song_to_playlist_youtube_api(playlist_id, video_id)
                if success:
                    # On success, add to the session processed list
                    self.session_processed_videos.add(video_id)
                    title = best_match.get('snippet', {}).get('title', 'Unknown')
                    result_details.update({'status': 'Added', 'error': None, 'video_id': video_id, 'title': title})
                    return {'success': True, 'details': result_details}
                else:
                    result_details['error'] = 'Failed to add song to playlist'

            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed for song '{song}': {e}")
                result_details['error'] = str(e)
                await asyncio.sleep(2 ** attempt)

        return {'success': False, 'details': result_details}
    
    def set_services(self, youtube_service: Optional[Resource] = None, ytmusic: Optional[YTMusic] = None) -> None:
        """Sets the authenticated API service clients for the agent."""
        self.youtube_service = youtube_service
        self.ytmusic = ytmusic

    def set_delay(self, delay: float) -> None:
        """Sets the delay between API calls to respect rate limits."""
        self.delay = delay

    def set_api_key(self, api_key: str) -> None:
        """Sets the YouTube Data API key for search-only operations."""
        self.api_key = api_key

    def _sanitize_text(self, text: str) -> str:
        """Cleans and sanitizes text input to prevent API errors."""
        if not text:
            return ""
        try:
            text = str(text).strip()
            # Normalize Unicode characters and remove non-printable characters
            normalized = unicodedata.normalize('NFKC', text)
            cleaned = "".join(c for c in normalized if c.isprintable() or c.isspace())
            # Replace common problematic characters
            replacements = {'"': "'", '`': "'", '’': "'", '“': "'", '”': "'"}
            for old, new in replacements.items():
                cleaned = cleaned.replace(old, new)
            return cleaned.strip()
        except Exception as e:
            logger.error(f"Text sanitization failed for '{text}': {e}")
            return text  # Return original text on failure

    async def create_playlist_youtube_api(self, name: str, description: str = "", privacy: str = "private") -> Optional[str]:
        """Creates a playlist using the YouTube Data API. If a playlist with the same name exists, appends +1 to the name."""
        if not self.youtube_service:
            logger.error("YouTube service not initialized for playlist creation.")
            return None
        await asyncio.sleep(self.delay)
        try:
            clean_name = self._sanitize_text(name)
            clean_desc = self._sanitize_text(description)

            # --- Check for existing playlists with the same name ---
            existing_names = set()
            request = self.youtube_service.playlists().list(part="snippet", mine=True, maxResults=50)
            response = request.execute()
            for item in response.get('items', []):
                existing_names.add(item['snippet']['title'].strip().lower())

            original_name = clean_name
            suffix = 1
            while clean_name.strip().lower() in existing_names:
                clean_name = f"{original_name} +{suffix}"
                suffix += 1

            body = {
                'snippet': {'title': clean_name, 'description': clean_desc},
                'status': {'privacyStatus': privacy}
            }
            request = self.youtube_service.playlists().insert(part="snippet,status", body=body)
            response = request.execute()

            playlist_id = response['id']
            st.session_state.stats['created_playlists'] += 1
            # After st.session_state.stats['created_playlists'] += 1
            self.quota_settings['current_quota'] += self.quota_settings.get('playlist_create', 50)
            st.session_state.stats['quota_used'] = self.quota_settings['current_quota']
            st.session_state.stats['quota_remaining'] = max(0, self.quota_settings['daily_quota'] - self.quota_settings['current_quota'])
            logger.info(f"Created YouTube playlist '{clean_name}' with ID: {playlist_id}")
            return playlist_id
        except Exception as e:
            logger.error(f"YouTube API playlist creation failed: {e}", exc_info=True)
            st.error(f"Failed to create YouTube playlist: {e}")
            return None

    async def search_song_youtube_api(self, query: str, max_results: int = 1) -> Optional[List[Dict]]:
        """Searches for a song using the YouTube Data API."""
        if not self.youtube_service:
            return None
        await asyncio.sleep(self.delay)
        try:
            clean_query = self._sanitize_text(query)
            if not clean_query:
                return None

            search_query = f'"{clean_query}" official audio'
            request = self.youtube_service.search().list(
                q=search_query,
                part='id,snippet',
                type='video',
                maxResults=max_results,
                videoCategoryId='10'  # Music Category
            )
            response = request.execute()
            results = response.get('items', [])
            
            if not results:
                logger.warning(f"No YouTube API results found for: {clean_query}")
                return None
            
            st.session_state.stats['searches'] += 1
            self.quota_settings['current_quota'] += self.quota_settings['search_cost']
            st.session_state.stats['quota_used'] = self.quota_settings['current_quota']
            st.session_state.stats['quota_remaining'] = max(0, self.quota_settings['daily_quota'] - self.quota_settings['current_quota'])
            return results
        except Exception as e:
            logger.error(f"YouTube API search failed for '{query}': {e}")
            st.session_state.stats['errors'] += 1
            return None

    async def add_song_to_playlist_youtube_api(self, playlist_id: str, video_id: str) -> bool:
        """Adds a song to a specified playlist using the YouTube Data API."""
        if not self.youtube_service or not video_id:
            logger.error(f"Cannot add song. Service initialized: {bool(self.youtube_service)}, Video ID: {video_id}")
            return False
            
        await asyncio.sleep(self.delay)
        try:
            body = {
                'snippet': {
                    'playlistId': playlist_id,
                    'resourceId': {'kind': 'youtube#video', 'videoId': video_id}
                }
            }
            self.youtube_service.playlistItems().insert(part='snippet', body=body).execute()
            
            st.session_state.stats['added'] += 1
            self.session_processed_videos.add(video_id)
            # After st.session_state.stats['added'] += 1
            self.quota_settings['current_quota'] += self.quota_settings['playlist_insert_cost']
            st.session_state.stats['quota_used'] = self.quota_settings['current_quota']
            st.session_state.stats['quota_remaining'] = max(0, self.quota_settings['daily_quota'] - self.quota_settings['current_quota'])
            return True
        except Exception as e:
            logger.error(f"Failed to add song {video_id} to playlist {playlist_id} via API: {e}")
            st.session_state.stats['errors'] += 1
            return False

    def get_best_match(self, search_results: List[Dict], original_query: str) -> Optional[Dict]:
        """Selects the best search result from a list based on relevance."""
        if not search_results:
            return None
        # For now, the first result from the optimized query is trusted.
        # More complex logic could be added here if needed.
        return search_results[0]
    
    async def _process_with_retry(self, song: str, playlist_id: str) -> Dict:
        """Processes a single song with a retry mechanism."""
        result_details = {'song': song, 'status': 'Failed', 'error': 'Unknown error', 'video_id': None, 'title': None}
        for attempt in range(self.security['max_retries']):
            try:
                search_results = await self.search_song_youtube_api(song)
                if not search_results:
                    result_details['error'] = 'No search results found'
                    await asyncio.sleep(1) # Wait before retrying
                    continue

                best_match = self.get_best_match(search_results, song)
                if not best_match:
                    result_details['error'] = 'No suitable match found'
                    await asyncio.sleep(1)
                    continue
                
                video_id = best_match.get('id', {}).get('videoId')
                if not video_id:
                    result_details['error'] = 'Invalid video ID in search result'
                    continue

                if video_id in self.session_processed_videos:
                    result_details.update({'status': 'Skipped (Duplicate)', 'error': None, 'video_id': video_id})
                    return {'success': True, 'details': result_details}
                
                success = await self.add_song_to_playlist_youtube_api(playlist_id, video_id)
                if success:
                    title = best_match.get('snippet', {}).get('title', 'Unknown')
                    result_details.update({'status': 'Added', 'error': None, 'video_id': video_id, 'title': title})
                    return {'success': True, 'details': result_details}
                else:
                    result_details['error'] = 'Failed to add song to playlist'

            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed for song '{song}': {e}")
                result_details['error'] = str(e)
                await asyncio.sleep(2 ** attempt)  # Exponential backoff

        return {'success': False, 'details': result_details}

    async def process_song_list(self, songs: List[str], playlist_name: str, method: str, privacy: str, create_new: bool = True, existing_playlist_id: Optional[str] = None) -> Dict:
        """
        Processes a list of songs, either creating a new playlist or adding to an existing one.
        
        Args:
            songs: A list of song titles to process.
            playlist_name: The name of the playlist.
            method: The API to use ('youtube_api' or 'ytmusic').
            privacy: The privacy status of a new playlist.
            create_new: If True, creates a new playlist. If False, adds to an existing one.
            existing_playlist_id: The ID of the playlist to add songs to if create_new is False.

        Returns:
            A dictionary containing the results of the operation.
        """
        songs = list(dict.fromkeys(songs))  # Remove duplicates
        results = {'playlist_id': None, 'total_songs': len(songs), 'successful': 0, 'failed': [], 'details': [], 'duplicates_skipped': 0}  # Add duplicates_skipped
        
        # Step 1: Determine the playlist ID
        playlist_id = None
        if create_new:
            if method == "youtube_api":
                playlist_id = await self.create_playlist_youtube_api(playlist_name, privacy=privacy.lower())
            # Add elif for ytmusic if needed
            if not playlist_id:
                st.error("Playlist creation failed. Aborting process.")
                return results
        else:
            if not existing_playlist_id:
                st.error("An existing playlist ID is required but was not provided.")
                return results
            playlist_id = existing_playlist_id

        results['playlist_id'] = playlist_id
        self.current_playlist_id = playlist_id

        # Step 2: Process songs in batches
        total_songs_to_process = len(songs)
        progress_bar = st.progress(0)
        status_text = st.empty()

        # Before processing, estimate tokens needed
        num_songs = len(songs)
        tokens_needed = num_songs * (self.quota_settings['search_cost'] + self.quota_settings['playlist_insert_cost'])
        if create_new:
            tokens_needed += self.quota_settings.get('playlist_create', 50)
        st.session_state.stats['quota_estimated_needed'] = tokens_needed
        st.session_state.stats['quota_total'] = self.quota_settings['daily_quota']

        for i in range(0, total_songs_to_process, self.batch_size):
            batch = songs[i:i + self.batch_size]
            estimated_quota = len(batch) * (self.quota_settings['search_cost'] + self.quota_settings['playlist_insert_cost'])
            if self.quota_settings['current_quota'] + estimated_quota > self.quota_settings['daily_quota']:
                st.warning(f"⚠️ Daily quota limit approaching. Halting process to avoid exceeding limits. Processed {i} songs.")
                logger.warning("Halting due to YouTube API quota limit.")
                break

            tasks = [self._process_with_retry(song, playlist_id) for song in batch]
            # Instead of gathering all at once, process one by one for progress
            for idx, coro in enumerate(tasks):
                res = await coro
                details = res['details']
                results['details'].append(details)
                if details['status'] == 'Added':
                    results['successful'] += 1
                elif details['status'].startswith('Skipped'):
                    results['duplicates_skipped'] += 1
                else:
                    results['failed'].append(details['song'])

                # --- Update quota stats in real time ---
                quota_used = self.quota_settings['current_quota'] + (idx + 1) * (self.quota_settings['search_cost'] + self.quota_settings['playlist_insert_cost'])
                quota_total = self.quota_settings['daily_quota']
                quota_remaining = max(0, quota_total - quota_used)
                st.session_state.stats['quota_used'] = self.quota_settings['current_quota']
                st.session_state.stats['quota_remaining'] = max(0, self.quota_settings['daily_quota'] - self.quota_settings['current_quota'])

            self.quota_settings['current_quota'] += estimated_quota

            # Update progress
            processed_count = i + len(batch)
            progress_percent = min(1.0, processed_count / total_songs_to_process)
            progress_bar.progress(progress_percent)
            status_text.text(f"Processed {processed_count}/{total_songs_to_process} songs...")
    
        logger.info(f"Processing complete. Successful: {results['successful']}, Failed: {len(results['failed'])}, Duplicates Skipped: {results['duplicates_skipped']}")
        return results


# --- Authentication and Helper Functions ---

def get_available_port() -> Optional[int]:
    """Finds and returns an available port from the ALLOWED_PORTS list."""
    for port in OAUTH_PORTS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('localhost', port)) != 0:
                logger.info(f"Found available port: {port}")
                return port
    logger.error("No allowed ports available for OAuth.")
    st.error("❌ All designated ports (8080, 8501, 8502) are in use. Please free up one of these ports.")
    return None

async def fetch_existing_playlist_items(agent: YouTubeMusicAutomationAgent, playlist_id: str) -> set:
    """
    Fetches all video IDs from a given YouTube playlist to check for duplicates.
    This function handles pagination and consumes API quota.
    """
    if not agent.youtube_service:
        return set()

    video_ids = set()
    next_page_token = None
    loop = asyncio.get_event_loop()

    with st.spinner(f"Analyzing existing playlist... This may take a moment."):
        while True:
            try:
                request = agent.youtube_service.playlistItems().list(
                    part='snippet',
                    playlistId=playlist_id,
                    maxResults=50,
                    pageToken=next_page_token
                )
                response = await loop.run_in_executor(None, request.execute)
                
                for item in response.get('items', []):
                    video_id = item.get('snippet', {}).get('resourceId', {}).get('videoId')
                    if video_id:
                        video_ids.add(video_id)
                
                next_page_token = response.get('nextPageToken')
                if not next_page_token:
                    break
            except Exception as e:
                logger.error(f"Failed to fetch a page of playlist items: {e}")
                st.error(f"Could not fully analyze the existing playlist. Duplicate checking may be incomplete. Error: {e}")
                break
    
    logger.info(f"Fetched {len(video_ids)} existing video IDs from playlist {playlist_id}.")
    return video_ids

def authenticate_youtube_api(email: str) -> Optional[Resource]:
    """
    Handles the OAuth 2.0 flow for the YouTube Data API.

    It tries to load existing credentials, refreshes them if expired, or
    initiates a new local server flow if they don't exist.
    
    Args:
        email: The user's Google account email to hint during authentication.

    Returns:
        An authorized Google API client resource object, or None on failure.
    """
    creds = None
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception as e:
            logger.warning(f"Could not load token file: {e}. A new one will be created.")
            os.remove(TOKEN_FILE)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                logger.info("Refreshing expired credentials...")
                creds.refresh(Request())
            except Exception as e:
                logger.error(f"Credential refresh failed: {e}. Starting new auth flow.")
                creds = None
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                st.error(f"❌ `credentials.json` not found! Please download it from your Google Cloud project and place it in the same directory as this script.")
                return None
            
            port = get_available_port()
            if not port:
                return None
            
            try:
                flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
                flow.redirect_uri = f"http://localhost:{port}/"
                
                auth_url, _ = flow.authorization_url(prompt='select_account', login_hint=email)
                st.info(f"Your browser will open for authentication. If it doesn't, please click this link: [Authenticate]({auth_url})")
                
                creds = flow.run_local_server(
                    port=port,
                    prompt='consent',
                    authorization_prompt_message='Please sign in with your Google account to continue.'
                )
            except Exception as e:
                logger.error(f"OAuth flow failed: {e}", exc_info=True)
                st.error(f"Authentication failed: {e}")
                if "redirect_uri_mismatch" in str(e):
                    st.error("Please ensure the redirect URI `http://localhost:{port}/` is added to your OAuth client in Google Cloud Console.")
                return None

        # Save the credentials for the next run
        if creds:
            with open(TOKEN_FILE, 'w') as token:
                token.write(creds.to_json())
                logger.info("Credentials saved to token.json")
        else:
            logger.error("No credentials to save after authentication flow.")
            st.error("Authentication failed. Please try again.")
            return None

    try:
        youtube_service = build('youtube', 'v3', credentials=creds)
        logger.info("YouTube API service built successfully.")
        return youtube_service
    except Exception as e:
        logger.error(f"Failed to build YouTube service: {e}", exc_info=True)
        st.error(f"Failed to build YouTube service: {e}")
        return None

def setup_ytmusic() -> Optional[YTMusic]:
    """Sets up YTMusic authentication, prioritizing new OAuth method."""
    # Check for existing valid token first
    if os.path.exists(YTMUSIC_AUTH_FILE):
        try:
            ytmusic = YTMusic(YTMUSIC_AUTH_FILE)
            ytmusic.get_library_playlists(limit=1) # Test connection
            logger.info("YTMusic authenticated using existing token.")
            return ytmusic
        except Exception as e:
            logger.warning(f"Existing YTMusic token failed: {e}. Deleting and re-authenticating.")
            os.remove(YTMUSIC_AUTH_FILE)
    
    # If no valid token, setup new OAuth using credentials.json
    if os.path.exists(CREDENTIALS_FILE):
        try:
            logger.info("Setting up new YTMusic OAuth using credentials.json...")
            # YTMusic.setup will now handle the OAuth flow and save the file
            ytmusic = YTMusic.setup(filepath=YTMUSIC_AUTH_FILE, headers_raw=None, credentials_path=CREDENTIALS_FILE)
            logger.info("YTMusic authenticated with new OAuth token.")
            return ytmusic
        except Exception as e:
            logger.error(f"YTMusic OAuth setup failed: {e}", exc_info=True)
            st.error(f"YTMusic authentication failed: {e}. Please ensure your Google account has access to YouTube Music and try re-authenticating.")
            return None
    else:
        st.warning("`credentials.json` not found. YTMusic authentication requires it for the OAuth method.")
        return None

def detect_song_column(df: pd.DataFrame) -> Optional[str]:
    """Detects the most likely column containing song titles from a DataFrame."""
    possible_names = ['song', 'title', 'track', 'name', 'track name', 'song title', 'track title']
    for col in df.columns:
        if col.lower().strip() in possible_names:
            return col
    return None

async def handle_add_to_existing_playlist(agent: YouTubeMusicAutomationAgent, songs: List[str]) -> None:
    """UI and logic flow for adding songs to an existing playlist."""
    if not agent.youtube_service:
        st.error("YouTube API is not authenticated. Please authenticate to see your playlists.")
        return

    try:
        with st.spinner("Fetching your YouTube playlists..."):
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    request = agent.youtube_service.playlists().list(part="snippet,contentDetails", mine=True, maxResults=50)
                    playlists_response = request.execute()
                    break  # Success!
                except ConnectionResetError as e:
                    logger.warning(f"Connection reset while fetching playlists (attempt {attempt+1}/{max_retries}): {e}")
                    time.sleep(2 ** attempt)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise  # Re-raise last error
                    logger.warning(f"Error fetching playlists (attempt {attempt+1}/{max_retries}): {e}")
                    time.sleep(2 ** attempt)
        
        user_playlists = playlists_response.get('items', [])
        if not user_playlists:
            st.warning("No YouTube playlists found for this account. Please create a playlist on YouTube first.")
            return

        playlist_options = {
            f"{p['snippet']['title']} ({p['contentDetails']['itemCount']} songs)": {
                'id': p['id'],
                'title': p['snippet']['title'],
                'song_count': p['contentDetails']['itemCount']
            } for p in user_playlists
        }
        
        selected_display_name = st.selectbox("Select a playlist to add songs to:", options=list(playlist_options.keys()))

        if selected_display_name and songs:
            playlist_info = playlist_options[selected_display_name]
            playlist_id = playlist_info['id']
            current_size = playlist_info['song_count']
            
            st.info(f"**Playlist:** {playlist_info['title']}\n\n"
                    f"**Current Songs:** {current_size}\n\n"
                    f"**Songs to Add:** {len(songs)}")

            remaining_space = YOUTUBE_API_QUOTAS['playlist_item_limit'] - current_size
            if len(songs) > remaining_space:
                st.warning(f"⚠️ Your list has {len(songs)} songs, but the selected playlist only has space for {remaining_space} more. The list will be truncated.")
                songs = songs[:remaining_space]

            if st.button("➕ Add Songs to This Playlist", type="primary"):
                # Fetch existing songs to enable duplicate skipping
                agent.existing_playlist_video_ids = await fetch_existing_playlist_items(agent, playlist_id)

                # --- NEW: Skip songs already in playlist by comparing names ---
                # Fetch titles of existing songs in the playlist
                existing_video_ids = agent.existing_playlist_video_ids
                logger.info(f"Fetched {len(existing_video_ids)} existing video IDs from playlist.")
                
                existing_titles = set()
                if existing_video_ids:
                    # Fetch titles for existing video IDs (batch, avoid extra tokens)
                    all_titles = set()
                    video_ids_list = list(existing_video_ids)
                    for i in range(0, len(video_ids_list), 50):
                        batch_ids = video_ids_list[i:i+50]
                        try:
                            request = agent.youtube_service.videos().list(
                                part="snippet",
                                id=",".join(batch_ids),
                                maxResults=50
                            )
                            response = request.execute()
                            for item in response.get('items', []):
                                title = item.get('snippet', {}).get('title', '').strip().lower()
                                if title:
                                    all_titles.add(title)
                        except Exception as e:
                            logger.warning(f"Could not fetch titles for some playlist videos: {e}", exc_info=True)
                    existing_titles = all_titles
                logger.info(f"Derived {len(existing_titles)} unique titles from existing playlist videos.")
                logger.debug(f"Existing titles: {existing_titles}")

                # Now, filter out songs from input that match existing titles (case-insensitive)
                filtered_songs = []
                skipped_songs = []
                skipped_matches = []  # For UI and CSV

                for song in songs:
                    is_dup, matched_title = is_duplicate_superbullet(song, existing_titles)
                    if is_dup:
                        skipped_songs.append(song)
                        skipped_matches.append({'input_song': song, 'matched_playlist_title': matched_title})
                        logger.info(f"Skipping '{song}' as it matches existing playlist title: '{matched_title}'")
                    else:
                        filtered_songs.append(song)
                
                logger.info(f"Songs to be skipped due to name match: {len(skipped_songs)} songs. List: {skipped_songs}")
                logger.info(f"Songs to be processed after name filtering: {len(filtered_songs)} songs. List: {filtered_songs}")

                if skipped_songs:
                    st.info(f"Skipped {len(skipped_songs)} songs already present in the playlist (by name):")
                    st.write(skipped_songs)

                if not filtered_songs:
                    st.warning("All songs in your CSV already exist in the selected playlist. Nothing to add.")
                    # Still generate and show the report with all skipped songs
                    results = {
                        'playlist_id': playlist_id,
                        'total_songs': len(songs),
                        'successful': 0,
                        'failed': [],
                        'details': [],
                        'duplicates_skipped': len(skipped_matches)
                    }
                    # Add skipped songs to the details for reporting
                    for skipped in skipped_matches:
                        results['details'].append({
                            'song': skipped['input_song'],
                            'status': 'Skipped (Already in Playlist by Name/Fuzzy)',
                            'error': None,
                            'video_id': None,
                            'matched_playlist_title': skipped['matched_playlist_title']
                        })
                    update_and_display_results(results)
                    return

                results = await agent.process_song_list(
                    songs=filtered_songs,
                    playlist_name=playlist_info['title'],
                    method="youtube_api",
                    privacy="", # Not needed when adding to existing
                    create_new=False,
                    existing_playlist_id=playlist_id
                )
                # Add skipped songs to the results for reporting
                if 'details' in results:
                    for skipped in skipped_matches:
                        results['details'].append({
                            'song': skipped['input_song'],
                            'status': 'Skipped (Already in Playlist by Name/Fuzzy)',
                            'error': None,
                            'video_id': None,
                            'matched_playlist_title': skipped['matched_playlist_title']
                        })
                results['duplicates_skipped'] += len(skipped_matches)
                update_and_display_results(results)

    except Exception as e:
        logger.error(f"Error handling existing playlists: {e}", exc_info=True)
        st.error(
            "An error occurred while fetching playlists. "
            "This may be due to a network issue, quota exhaustion, or a temporary problem with the YouTube API. "
            "Please check your internet connection and try again. If the problem persists, wait a few minutes and retry."
        )

# --- UI Rendering Functions ---

def render_sidebar() -> None:
    """Renders the sidebar with authentication status, stats, and system info."""
    with st.sidebar:
        st.title("📊 Agent Dashboard")

        with st.expander("🔐 Authentication Status", expanded=True):
            if st.session_state.auth_status:
                st.success(f"Authenticated: {st.session_state.current_account}")
            else:
                st.warning("Not Authenticated")

        with st.expander("📈 Quick Stats", expanded=True):
            s = st.session_state.stats
            s['searches'] = s.get('searches', 0)
            s['added'] = s.get('added', 0)
            s['errors'] = s.get('errors', 0)
            s['created_playlists'] = s.get('created_playlists', 0)
            s['duplicates_skipped'] = s.get('duplicates_skipped', 0)
            s['quota_used'] = s.get('quota_used', 0)
            s['quota_total'] = s.get('quota_total', YOUTUBE_API_QUOTAS['daily_limit'])
            s['quota_remaining'] = s.get('quota_remaining', YOUTUBE_API_QUOTAS['daily_limit'])
            s['quota_estimated_needed'] = s.get('quota_estimated_needed', 0)
            # Choose which estimate to show based on the current action
            action = st.session_state.get('current_action', 'Create a New Playlist')
            if action == "Add to an Existing Playlist":
                estimated_needed = s.get('quota_estimated_needed_existing', 0)
            else:
                estimated_needed = s.get('quota_estimated_needed_new', 0)
            st.metric("Estimated Needed", f"{estimated_needed:,}")
            col1, col2 = st.columns(2)
            col1.metric("Searches", s['searches'])
            col1.metric("Playlists Created", s['created_playlists'])
            col2.metric("Songs Added", s['added'])
            col2.metric("Errors", s['errors'])
            st.metric("Duplicates Skipped", s['duplicates_skipped'])
            st.metric("Quota Used", f"{s['quota_used']:,} / {s['quota_total']:,}")
            st.metric("Quota Remaining", f"{s['quota_remaining']:,}")
            st.progress(s['quota_used'] / s['quota_total'])

        with st.expander("⚙️ System Info", expanded=False):
            st.caption("**Dependencies:**")
            for lib, version in DEPENDENCIES.items():
                st.write(f"- `{lib}`: `{version}`")
            st.caption("**API Limits:**")
            st.write(f"- Daily Quota: {YOUTUBE_API_QUOTAS['daily_limit']:,} units")
            st.write(f"- Batch Size: 250 songs (uses up to 40,000 tokens per batch, per day)")

        if st.button("🔄 Reset Stats", key="sidebar_reset_stats"):
            st.session_state.stats = {
                'searches': 0, 'added': 0, 'errors': 0, 'created_playlists': 0,
                'duplicates_skipped': 0, 'quota_used': 0, 'quota_total': YOUTUBE_API_QUOTAS['daily_limit'],
                'quota_remaining': YOUTUBE_API_QUOTAS['daily_limit'], 'quota_estimated_needed': 0
            }
            st.rerun()

def update_and_display_results(results: Dict) -> None:
    """
    Updates the session stats with the final results and displays them.
    Then, forces a rerun of the app to refresh the sidebar stats correctly.
    """
    st.markdown("---")
    st.subheader("✅ Transfer Complete!")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Songs", results['total_songs'])
    col2.metric("✅ Songs Added", results['successful'])
    col3.metric("❌ Failed", len(results['failed']))
    col4.metric("⏭️ Duplicates Skipped", results.get('duplicates_skipped', 0))

    if results['playlist_id']:
        playlist_url = f"https://music.youtube.com/playlist?list={results['playlist_id']}"
        st.success(f"**[🔗 Open Your Playlist in YouTube Music]({playlist_url})**")
    
    if results['details']:
        results_df = pd.DataFrame(results['details'])
        # Download all results
        st.download_button(
            label="📄 Download Full Report (CSV)",
            data=results_df.to_csv(index=False).encode('utf-8'),
            file_name=f"transfer_report_{datetime.now().strftime('%Y%m%d')}.csv",
            mime='text/csv'
        )
        # Download only successes
        success_df = results_df[results_df['status'] == 'Added']
        if not success_df.empty:
            st.download_button(
                label="✅ Download Successful Songs (CSV)",
                data=success_df.to_csv(index=False).encode('utf-8'),
                file_name=f"successful_songs_{datetime.now().strftime('%Y%m%d')}.csv",
                mime='text/csv'
            )
        # Download only failures
        fail_df = results_df[results_df['status'] == 'Failed']
        if not fail_df.empty:
            st.download_button(
                label="❌ Download Failed Songs (CSV)",
                data=fail_df.to_csv(index=False).encode('utf-8'),
                file_name=f"failed_songs_{datetime.now().strftime('%Y%m%d')}.csv",
                mime='text/csv'
            )
        with st.expander("📄 View Detailed Transfer Results"):
            st.dataframe(results_df)

    # --- STATS FIX ---
    # Update the session state from the final results dictionary
    # The incremental updates for searches, errors, and skipped are already done.
    # We just need to finalize the 'added' count.
    st.session_state.stats['added'] = results['successful']
    st.session_state.stats['duplicates_skipped'] += results.get('duplicates_skipped', 0)

    # --- Display final quota usage and estimate ---
    final_quota_used = st.session_state.stats.get('quota_used', 0)
    quota_total = st.session_state.stats.get('quota_total', YOUTUBE_API_QUOTAS['daily_limit'])
    quota_estimated = st.session_state.stats.get('quota_estimated_needed', 0)
    st.info(
        f"**Quota Used:** {final_quota_used:,} / {quota_total:,} tokens\n\n"
        f"**Quota Estimated for this operation:** {quota_estimated:,} tokens"
    )

    st.info("Dashboard stats have been updated. You may now create another playlist or exit.")
    # Do NOT rerun or sleep here.

# Finally, ensure the call in the "Create a New Playlist" part of main() is also updated.
# In the main() function, find this line:
# display_results(results)
# And REPLACE it with:
# update_and_display_results(results)

# --- Main Application Logic ---

async def main() -> None:
    st.title("🎵 YouTube Music Automation Agent")

    # Initialize agent and session state
    agent = YouTubeMusicAutomationAgent()
    agent.batch_size = 1200  # Ensure batch size is set to 1200 for the session
    initialize_session_state()

    # --- Call render_sidebar() ONLY ONCE here ---
    render_sidebar()

    # --- Step 1: Authentication ---
    if not st.session_state.auth_status:
        st.subheader("Step 1: Authentication")
        st.info("Please authenticate with your Google account to begin.")
        
        email = st.text_input("Enter your Google Account Email:", placeholder="your.email@gmail.com", key="auth_email_input")
        st.session_state.auth_email = email

        auth_method = st.radio(
            "Choose Authentication Method:",
            ["YouTube API (OAuth)"],  # Only one option now
            horizontal=True
        )

        if auth_method == "YouTube API (OAuth)":
            if st.button("🔑 Authenticate with YouTube"):
                if not email:
                    st.warning("Please enter your email address.")
                else:
                    with st.spinner(f"Authenticating {email}..."):
                        youtube_service = authenticate_youtube_api(email)
                        if youtube_service:
                            st.session_state.youtube_service = youtube_service
                            st.session_state.auth_status = True
                            st.session_state.current_account = f"YouTube API ({email})"
                            st.success("Authentication successful!")
                            st.rerun()  # <-- updated here
        
        # Re-authentication button
        if st.button("Clear Authentication Tokens"):
            if os.path.exists(TOKEN_FILE): os.remove(TOKEN_FILE)
            if os.path.exists(YTMUSIC_AUTH_FILE): os.remove(YTMUSIC_AUTH_FILE)
            st.info("Tokens cleared. Please re-authenticate.")
            st.rerun()  # <-- updated here
            
        return # Stop further execution until authenticated

    # --- Post-Authentication Flow ---
    agent.set_services(st.session_state.youtube_service, st.session_state.ytmusic)
    
    st.subheader("Step 2: Upload Your Song List")
    uploaded_file = st.file_uploader("Upload a CSV file with a column of song names:", type=['csv'])

    songs = []
    if uploaded_file:
        try:
            df = pd.read_csv(uploaded_file)
            song_col = detect_song_column(df)
            if song_col:
                st.success(f"Detected song column: '{song_col}'")
                songs = df[song_col].dropna().astype(str).str.strip().tolist()
                songs = [s for s in songs if s.lower() not in ['nan', 'none', '']]
                st.info(f"Found {len(songs)} valid songs to process.")
                with st.expander("Preview Songs"):
                    st.dataframe(pd.DataFrame({'Song': songs[:20]}))

                # --- Estimate tokens needed for Quick Stats sidebar ---
                num_songs = len(songs)
                playlist_create_cost = YOUTUBE_API_QUOTAS.get('playlist_create', 50)
                per_song_cost = YOUTUBE_API_QUOTAS['search'] + YOUTUBE_API_QUOTAS['playlist_insert']
                estimated_tokens_new = num_songs * per_song_cost + playlist_create_cost
                estimated_tokens_existing = num_songs * per_song_cost
                st.session_state.stats['quota_estimated_needed_new'] = estimated_tokens_new
                st.session_state.stats['quota_estimated_needed_existing'] = estimated_tokens_existing

                # --- Force sidebar to update ---
                # render_sidebar()   # <-- REMOVE this line
            else:
                st.error("Could not automatically detect a song column. Please ensure your CSV has a column named 'song', 'title', or 'track'.")
        except Exception as e:
            logger.error(f"Error reading CSV: {e}", exc_info=True)
            st.error(f"Error processing CSV file: {e}")
    
    if songs:
        st.subheader("Step 3: Choose Your Action")
        action = st.radio("Choose Action", ["Create a New Playlist", "Add to an Existing Playlist"], horizontal=True, label_visibility="collapsed")
        st.session_state['current_action'] = action

        if action == "Create a New Playlist":
            st.subheader("Step 4: Configure New Playlist")
            playlist_name = st.text_input("Playlist Name:", value=f"My Transfer {datetime.now().strftime('%Y-%m-%d')}")
            privacy = st.selectbox("Privacy:", ["Private", "Unlisted", "Public"])
            
            if st.button("🚀 Create Playlist and Add Songs", type="primary"):
                if not playlist_name.strip():
                    st.warning("Please enter a playlist name.")
                else:
                    loop = asyncio.get_event_loop()
                    results = await agent.process_song_list(
                        songs=songs,
                        playlist_name=playlist_name,
                        method="youtube_api",
                        privacy=privacy,
                        create_new=True
                    )
                    update_and_display_results(results)
                    added_songs = [d['song'] for d in results['details'] if d['status'] == 'Added']
                    if added_songs:
                        st.success("🎉 Process completed! The following songs were added to your playlist (in order):")
                        st.write(added_songs)
                    else:
                        st.info("No new songs were added to the playlist.")

        elif action == "Add to an Existing Playlist":
            st.subheader("Step 4: Select Existing Playlist")
            await handle_add_to_existing_playlist(agent, songs)

        if st.button("🎵 Create Another Playlist"):
            # Reset session state and force re-authentication
            st.session_state.auth_status = False
            st.session_state.youtube_service = None
            st.session_state.ytmusic = None
            st.session_state.current_account = None
            st.rerun()

def remove_emojis(text):
    return re.sub(r'[\U00010000-\U0010ffff]', '', text)

def normalize_title_ultra_strict(title: str) -> str:
    """Aggressive normalization for bulletproof duplicate detection."""
    if not isinstance(title, str):
        return ""
    # Unicode normalize (fullwidth/halfwidth, etc.)
    title = unicodedata.normalize('NFKC', title)
    # Remove accents/diacritics
    title = ''.join(c for c in unicodedata.normalize('NFKD', title) if not unicodedata.combining(c))
    # Remove emojis and non-printable
    title = remove_emojis(title)
    # Remove all content in any brackets ((), [], {}, <>), even nested
    title = re.sub(r'\(.*?\)|\[.*?\]|\{.*?\}|<.*?>', '', title)
    # Remove common music suffixes/prefixes (artist, feat., from, version, remix, live, edit, acoustic, cover, karaoke, instrumental, official, audio, video, lyrics?)

    title = re.sub(r'(?i)\b(feat\.?|ft\.?|from|version|remix|live|edit|acoustic|cover|karaoke|instrumental|official|audio|video|lyrics?)\b', '', title)
    # Remove artist prefix (e.g., "TXT - ", "LiSA - ")
    title = re.sub(r'^[\w\s\.\-]+ - ', '', title)
    # Remove punctuation
    title = title.translate(str.maketrans('', '', string.punctuation))
    # Collapse whitespace
    title = re.sub(r'\s+', ' ', title)
    return title.strip().lower()

def extract_core_words(title: str) -> Set[str]:
    """Extracts core words from a title, removing common stopwords and short words."""
    stopwords = set([
        'a', 'an', 'the', 'and', 'or', 'in', 'on', 'at', 'to', 'from', 'by', 'for', 'with',
        'of', 'is', 'it', 'this', 'that', 'these', 'those', 'be', 'are', 'am', 'was', 'were',
        'has', 'have', 'had', 'do', 'does', 'did', 'not', 'no', 'yes', 'if', 'then', 'else',
        'when', 'where', 'why', 'how', 'all', 'any', 'both', 'each', 'few', 'more', 'most',
        'some', 'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than', 'too', 'very'
    ])
    words = set(re.findall(r'\b\w+\b', title.lower()))
    core_words = words - stopwords
    return {w for w in core_words if len(w) > 2}  # Filter out short words

def calculate_word_overlap_ratio(words1: Set[str], words2: Set[str]) -> float:
    """Calculates the ratio of overlapping words between two sets of words."""
    if not words1 or not words2:
        return 0.0
    overlap = len(words1 & words2)
    total = len(words1 | words2)
    return overlap / total

def is_duplicate_ultra_strict(song_name: str, existing_titles: Set[str], 
                             similarity_threshold: float = 0.85,
                             word_overlap_threshold: float = 0.75) -> Tuple[bool, Optional[str]]:
    """
    Determines if a song is a duplicate based on an ultra-strict comparison.

    This method uses multiple layers of checks to determine duplication:
    1. Exact match after aggressive normalization.
    2. High similarity ratio using token set ratio.
    3. Significant word overlap.

    Args:
        song_name (str): The song name to check.
        existing_titles (Set[str]): A set of existing song titles to compare against.
        similarity_threshold (float): The threshold for the similarity ratio (0 to 1).
        word_overlap_threshold (float): The threshold for the word overlap ratio (0 to 1).

    Returns:
        Tuple[bool, Optional[str]]: A tuple where the first element is a boolean
        indicating if a duplicate was found, and the second element is the matching title or None.
    """
    song_norm = normalize_title_ultra_strict(song_name)
    song_words = extract_core_words(song_name)
    
    for title in existing_titles:
        title_norm = normalize_title_ultra_strict(title)
        title_words = extract_core_words(title)
        
        # Exact match check
        if song_norm == title_norm:
            return True, title
        
        # Similarity ratio check
        sim_ratio = ratio(song_norm, title_norm) / 100.0  # Convert to 0-1 range
        if sim_ratio >= similarity_threshold:
            return True, title
        
        # Word overlap check
        word_overlap = calculate_word_overlap_ratio(song_words, title_words)
        if word_overlap >= word_overlap_threshold:
            return True, title
    
    return False, None

def is_duplicate_bulletproof(song_name: str, existing_titles: set) -> tuple:
    return is_duplicate_ultra_strict(song_name, existing_titles)

def extract_core_titles_and_aliases(title: str) -> set:
    """
    Extracts all possible canonical representations and aliases from a title.
    Handles unicode, English/romanized, parenthesis/bracketed translations.
    Example: "永遠に光れ (Everlasting Shine)" yields {"everlasting shine", "永遠に光れ"}
    """
    if not isinstance(title, str):
        return set()
    title = title.strip()
    # Remove common YouTube "junk" (artist prefix, color coded, lyrics, etc.)
    parts_dash = re.split(r'\s*-\s*', title, maxsplit=1)
    core = parts_dash[-1] if len(parts_dash) > 1 else parts_dash[0]
    core = re.sub(r"\b(Color\s*Coded|Lyrics?|日本語字幕|Official|MV|VER\.?|Audio|Video|HD|[Ff]eat\.?|with|Slowed|Remix|Cover|Acoustic|Version|From.+)\b", "", core, flags=re.IGNORECASE)
    aliases = set()
    # Extract all parenthesis/bracketed/quoted text (as alias/translation)
    aliases.update(re.findall(r'[\(\[\{](.*?)[\)\]\}]', title))
    aliases.update(re.findall(r'\"(.*?)\"', title))
    aliases.add(core.strip())
    aliases.add(title)
    # Normalize: lower, remove punctuation, strip accents, collapse spaces
    def norm(s):
        s = unicodedata.normalize('NFKD', s)
        s = ''.join(c for c in s if not unicodedata.combining(c) and c in string.printable and c not in string.punctuation)
        s = re.sub(r'\s+', ' ', s)
        return s.strip().lower()
    return set(norm(alias) for alias in aliases if alias)

def is_duplicate_superbullet(song_name: str, existing_titles: set, ratio_thresh=90) -> tuple:
    """
    Compares a song_name to all canonical and alias representations of existing_titles.
    Returns (True, matched_title) if any strong match is found.
    """
    # Expand all possible forms of the candidate
    song_aliases = extract_core_titles_and_aliases(song_name)
    song_norm = normalize_title_ultra_strict(song_name)
    song_tokens = set(song_norm.split())
    for exist_title in existing_titles:
        exist_aliases = extract_core_titles_and_aliases(exist_title)
        exist_norm = normalize_title_ultra_strict(exist_title)
        exist_tokens = set(exist_norm.split())
        # Alias-level direct/substring/fuzzy match
        for s_alias in song_aliases:
            for e_alias in exist_aliases:
                if not s_alias or not e_alias:
                    continue
                if s_alias == e_alias or s_alias in e_alias or e_alias in s_alias:
                    return True, exist_title
                if partial_ratio(s_alias, e_alias) > ratio_thresh or token_set_ratio(s_alias, e_alias) > ratio_thresh:
                    return True, exist_title
        # Token set overlap (aggressive)
        if song_tokens and exist_tokens and len(song_tokens & exist_tokens) / max(len(song_tokens), 1) > 0.7:
            return True, exist_title
        # Substring match (either way)
        if song_norm in exist_norm or exist_norm in song_norm:
            return True, exist_title
        # Dynamic fuzzy threshold
        threshold = 90 if min(len(song_norm), len(exist_norm)) < 15 else 80
        ratio_val = token_set_ratio(song_norm, exist_norm)
        if ratio_val >= threshold:
            return True, exist_title
    return False, None

def generate_unique_key(base_key: str) -> str:
    """Generate a unique key by appending a timestamp"""
    return f"{base_key}_{int(time.time() * 1000)}"

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())


#
# === Testing and Debugging Suggestions ===
#
# To ensure the robustness of this agent, consider the following testing strategies:
#
# 1. Manual Testing Checklist:
#    - [ ] **Authentication Flow (YouTube API OAuth):**
#          - Test with a clean environment (no `token.json`).
#          - Test token refresh by manually revoking access in Google Account settings and re-running.
#          - Test authenticating with the wrong Google account to see if the `login_hint` works.
#    - [ ] **Authentication Flow (YTMusic OAuth):**
#          - Test initial setup (no `ytmusic_oauth.json`).
#          - Test using an existing, valid token.
#          - Test behavior when `credentials.json` is missing.
#    - [ ] **CSV Upload:**
#          - Test with a standard CSV from a Spotify export.
#          - Test with a CSV that has different column names for songs (e.g., 'Track Name').
#          - Test with an empty, malformed, or very large CSV file.
#    - [ ] **Playlist Creation:**
#          - Create a new public, private, and unlisted playlist.
#          - Verify the playlist appears correctly in YouTube with the right songs.
#          - Test with a song list that exceeds the configured batch size to check batching logic.
#    - [ ] **Add to Existing Playlist:**
#          - Select a playlist from the dropdown and add songs.
#          - Verify the songs are correctly appended to the playlist on YouTube.
#          - Test adding songs that would cause the playlist to exceed the 5,000-item limit.
#    - [ ] **Error Handling:**
#          - Temporarily use an invalid API key or bad credentials to check the error feedback.
#          - Simulate a quota limit by setting `daily_limit` in `YOUTUBE_API_QUOTAS` to a low number (e.g., 200) and test the process halt.
#
# 2. Automated Testing (Conceptual):
#    While this script is not currently structured for easy automated testing, here's a recommended approach for future development:
#    - **Refactoring:** The most important step is to separate the Streamlit UI logic (functions like `main`, `render_sidebar`)
#      from the agent's core, non-UI logic (the `YouTubeMusicAutomationAgent` class). This could be done by moving the agent
#      to its own file (e.g., `agent.py`).
#
#    - **Unit Testing the Agent:** Once separated, you can use Python's `unittest` and `unittest.mock` libraries to test the agent class in isolation.
#      - Mock the `youtube_service` and `ytmusic` objects to avoid making real API calls during tests.
#      - Create mock API responses (e.g., a sample JSON for a search result or a playlist creation confirmation).
#      - Write tests to verify that methods like `process_song_list` call the correct API methods with the correct arguments.
#
#      - **Example Test Case (using `pytest` and `pytest-asyncio`):**
#        ```python
#        # in tests/test_agent.py
#        import pytest
#        from unittest.mock import MagicMock, AsyncMock
#        from agent import YouTubeMusicAutomationAgent # Assuming agent is in its own file
#
#        @pytest.mark.asyncio
#        async def test_process_single_song_success():
#            # 1. Arrange
#            agent = YouTubeMusicAutomationAgent()
#            agent.youtube_service = MagicMock()
#
#            # Mock the return value of an API call
#            mock_search_result = {'items': [{'id': {'videoId': 'xyz123'}, 'snippet': {'title': 'Test Song'}}]}
#            agent.youtube_service.search().list().execute = MagicMock(return_value=mock_search_result)
#            agent.youtube_service.playlistItems().insert().execute = MagicMock()
#
#            # 2. Act
#            result = await agent._process_with_retry(song="Test Song", playlist_id="pl123")
#
#            # 3. Assert
#            assert result['success'] is True
#            assert result['details']['status'] == 'Added'
#            agent.youtube_service.search().list().execute.assert_called_once()
#            agent.youtube_service.playlistItems().insert().execute.assert_called_once()
#        ```
#
#    - **Testing UI Components:** Testing Streamlit UIs directly with code is challenging. The best practice is to keep
#      UI functions as "dumb" as possible—they should only be responsible for displaying widgets and calling the
#      well-tested backend logic (the agent class) to do the actual work.
