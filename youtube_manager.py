import streamlit as st
import pandas as pd
import os
import logging
import json
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import unicodedata
from ytmusicapi import YTMusic
import traceback
import sys
import tempfile
import webbrowser
import time
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
import socket
import psutil
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants
CREDENTIALS_FILE = 'credentials.json'
TOKEN_FILE = 'token.json'
YTMUSIC_AUTH_FILE = 'ytmusic_oauth.json'

ALLOWED_PORTS = [8501, 8080, 8502]  # Add more fallback ports
REDIRECT_URIS = {
    8080: "http://localhost:8080/",
    8501: "http://localhost:8501/",
    8502: "http://localhost:8502/"
}

# OAuth Configuration - Fixed ports
OAUTH_PORTS = [8080, 8501]  # Try these ports in order

# YouTube API Scopes
SCOPES = [
    'https://www.googleapis.com/auth/youtube',
    'https://www.googleapis.com/auth/youtube.force-ssl'
]

# Add these constants at the top of your file
YOUTUBE_API_QUOTAS = {
    'search': 100,  # units per search request
    'playlist_insert': 50,  # units per playlist insertion
    'playlist_create': 50,  # units per playlist creation
    'daily_limit': 10000,  # total daily quota
    'playlist_item_limit': 5000  # max items per playlist
}

# Import version info
import streamlit as st
import pandas as pd
from ytmusicapi import __version__ as ytmusic_version

# Replace the DEPENDENCIES constant
DEPENDENCIES = {
    'streamlit': st.__version__,
    'pandas': pd.__version__,
    'ytmusicapi': ytmusic_version,
    'google-api-python-client': 'v2.108.0',
    'google-auth-oauthlib': 'v1.2.0'
}

# Page config
st.set_page_config(
    page_title="🎵 YouTube Music Automation Agent",
    page_icon="🎵",
    layout="wide"
)

# Initialize session state
session_vars = {
    'youtube_service': None,
    'ytmusic': None,
    'auth_status': False,
    'current_account': None,
    'api_key': None,
    'stats': {'searches': 0, 'added': 0, 'errors': 0, 'created_playlists': 0}
}

for var, default in session_vars.items():
    if var not in st.session_state:
        st.session_state[var] = default

class YouTubeMusicAutomationAgent:
    """Enhanced YouTube Music automation agent with dual authentication"""
    
    def __init__(self):
        # Performance Metrics
        self.metrics = {
            'response_times': [],
            'success_count': 0,
            'error_count': 0,
            'rate_limit_hits': 0
        }
        
        # Security Settings
        self.security = {
            'max_retries': 3,
            'timeout': 30,
            'max_requests_per_minute': 60
        }

        # Rate Limiting
        self.rate_limit = {
            'window_size': 60,  # seconds
            'max_requests': 60,
            'requests': [],
            'last_reset': time.time()
        }

        # Quota Management
        self.quota_settings = {
            'daily_quota': 10000,
            'search_cost': 100,  # Cost per search
            'playlist_insert_cost': 50,  # Cost per playlist item insert
            'current_quota': 0
        }
        
        # Processed videos tracking
        self.processed_videos = set()

    def set_services(self, youtube_service=None, ytmusic=None):
        """Set YouTube API and YTMusic services"""
        self.youtube_service = youtube_service
        self.ytmusic = ytmusic
    
    def set_delay(self, delay: float):
        self.delay = delay
    
    def set_api_key(self, api_key: str):
        self.api_key = api_key
    
    def _sanitize_text(self, text: str) -> str:
        """Clean and sanitize text input"""
        try:
            if not text:
                return ""
            
            text = str(text).strip()
            normalized = unicodedata.normalize('NFKC', text)
            cleaned = ''.join(char for char in normalized if char.isprintable() or char.isspace())
            
            # Replace special characters
            replacements = {'–': '-', '—': '-', ''': "'", ''': "'", '"': '"', '"': '"'}
            for old, new in replacements.items():
                cleaned = cleaned.replace(old, new)
            
            return cleaned.strip()
        except Exception as e:
            logger.error(f"Text sanitization failed: {e}")
            return str(text)
    
    async def create_playlist_youtube_api(self, name: str, description: str = "", privacy: str = "private") -> str:
        """Create playlist using YouTube Data API"""
        await asyncio.sleep(self.delay)
        
        try:
            clean_name = self._sanitize_text(name)
            clean_desc = self._sanitize_text(description)
            
            playlist_body = {
                'snippet': {
                    'title': clean_name,
                    'description': clean_desc
                },
                'status': {
                    'privacyStatus': privacy
                }
            }
            
            response = self.youtube_service.playlists().insert(
                part='snippet,status',
                body=playlist_body
            ).execute()
            
            playlist_id = response['id']
            st.session_state.stats['created_playlists'] += 1
            logger.info(f"Created playlist via API: {clean_name}")
            return playlist_id
            
        except Exception as e:
            logger.error(f"Playlist creation failed: {e}")
            raise
    
    async def create_playlist_ytmusic(self, name: str, description: str = "", privacy: str = "PRIVATE") -> str:
        """Create playlist using YTMusic"""
        await asyncio.sleep(self.delay)
        
        try:
            clean_name = self._sanitize_text(name)
            clean_desc = self._sanitize_text(description)
            
            playlist_id = self.ytmusic.create_playlist(
                clean_name,
                clean_desc,
                privacy_status=privacy
            )
            
            st.session_state.stats['created_playlists'] += 1
            logger.info(f"Created playlist via YTMusic: {clean_name}")
            return playlist_id
            
        except Exception as e:
            logger.error(f"Playlist creation failed: {e}")
            raise
    
    async def search_song_youtube_api(self, query: str, max_results: int = 1) -> Optional[List[Dict]]:
        """Search for songs using YouTube Data API (optimized)"""
        await asyncio.sleep(self.delay)
        
        try:
            clean_query = self._sanitize_text(query)
            if not clean_query:
                return None
            
            # Improve search query
            search_query = f"{clean_query} official audio"
            
            search_response = self.youtube_service.search().list(
                q=search_query,
                part='id,snippet',
                type='video',
                maxResults=max_results,
                videoCategoryId='10',  # Music category
                fields='items(id/videoId,snippet/title)'
            ).execute()
            
            results = search_response.get('items', [])
            if not results:
                logger.warning(f"No results found for: {clean_query}")
                return None
                
            st.session_state.stats['searches'] += 1
            return results
            
        except Exception as e:
            logger.error(f"YouTube API search failed for '{query}': {e}")
            st.session_state.stats['errors'] += 1
            return None
    
    async def search_song_ytmusic(self, query: str) -> Optional[List[Dict]]:
        """Search for songs using YTMusic"""
        await asyncio.sleep(self.delay)
        
        try:
            clean_query = self._sanitize_text(query)
            if not clean_query:
                return None
            
            results = self.ytmusic.search(clean_query, filter="songs", limit=5)
            st.session_state.stats['searches'] += 1
            return results if results else None
            
        except Exception as e:
            logger.error(f"YTMusic search failed for '{query}': {e}")
            st.session_state.stats['errors'] += 1
            return None
    
    def _update_status(self, current: int, total: int):
        """Update progress status"""
        try:
            # Ensure progress is between 0 and 1
            progress = min(1.0, max(0.0, current / total))
            
            # Update progress bar
            st.progress(progress)
            
            # Show status message
            st.write(f"Processing: {current}/{total} songs ({progress*100:.1f}%)")
            
        except Exception as e:
            logger.error(f"Error updating status: {e}")

    async def add_song_to_playlist_youtube_api(self, playlist_id: str, video_id: str) -> bool:
        """Add song to playlist using YouTube Data API"""
        await asyncio.sleep(self.delay)
        
        try:
            if not video_id:
                logger.error("No video ID provided")
                return False
                
            playlist_item = {
                'snippet': {
                    'playlistId': playlist_id,
                    'resourceId': {
                        'kind': 'youtube#video',  # Required
                        'videoId': video_id
                    },
                    'position': 0  # Add at the beginning
                }
            }
            
            request = self.youtube_service.playlistItems().insert(
                part='snippet',
                body=playlist_item
            )
            response = request.execute()
            
            if response and 'id' in response:
                st.session_state.stats['added'] += 1
                return True
                
            return False
            
        except Exception as e:
            logger.error(f"Failed to add song {video_id} via API: {e}")
            st.session_state.stats['errors'] += 1
            return False

    async def add_song_to_playlist_ytmusic(self, playlist_id: str, video_id: str) -> bool:
        """Add song to playlist using YTMusic"""
        await asyncio.sleep(self.delay)
        
        try:
            self.ytmusic.add_playlist_items(playlist_id, [video_id])
            st.session_state.stats['added'] += 1
            return True
            
        except Exception as e:
            logger.error(f"Failed to add song {video_id} via YTMusic: {e}")
            st.session_state.stats['errors'] += 1
            raise
    
    def get_best_match(self, search_results: List[Dict], original_query: str, search_type: str = "ytmusic") -> Optional[Dict]:
        """Get the best match from search results"""
        if not search_results:
            return None
        
        # For YTMusic, return first result as it's usually the best match
        if search_type == "ytmusic":
            return search_results[0]
        
        # For YouTube API, apply some basic filtering
        if search_type == "youtube_api":
            for result in search_results:
                title = result['snippet']['title'].lower()
                query_lower = original_query.lower()
                
                # Basic relevance check
                if any(word in title for word in query_lower.split() if len(word) > 2):
                    return {
                        'videoId': result['id']['videoId'],
                        'title': result['snippet']['title'],
                        'channelTitle': result['snippet']['channelTitle']
                    }
            
            # Return first result if no good match found
            return {
                'videoId': search_results[0]['id']['videoId'],
                'title': search_results[0]['snippet']['title'],
                'channelTitle': search_results[0]['snippet']['channelTitle']
            }
        
        return None
    
    async def process_batch(self, songs: List[str], start_idx: int) -> Dict:
        """Process a batch of songs with rate limiting and error recovery"""
        batch_results = {
            'successful': 0,
            'failed': [],
            'details': []
        }

        for idx, song in enumerate(songs[start_idx:start_idx + self.batch_size]):
            try:
                # Rate limiting check
                await self._check_rate_limit()

                # Search and add song with retry logic
                result = await self._process_with_retry(song)
                
                if result['success']:
                    batch_results['successful'] += 1
                else:
                    batch_results['failed'].append(song)
                
                batch_results['details'].append(result['details'])

                # Update status monitoring
                self._update_status(start_idx + idx + 1, len(songs))

            except Exception as e:
                logger.error(f"Error processing {song}: {e}")
                batch_results['failed'].append(song)

        return batch_results

    async def _track_request_time(self, start_time: float):
        """Track API request response time"""
        response_time = time.time() - start_time
        self.metrics['response_times'].append(response_time)
        
        # Calculate and log average response time
        avg_response_time = sum(self.metrics['response_times']) / len(self.metrics['response_times'])
        logger.info(f"Average response time: {avg_response_time:.2f}s")

    async def _check_rate_limit(self) -> bool:
        """Enhanced rate limit checking with window sliding"""
        current_time = time.time()
        window_start = current_time - self.rate_limit['window_size']
        
        # Clean old requests
        self.rate_limit['requests'] = [
            req_time for req_time in self.rate_limit['requests'] 
            if req_time > window_start
        ]
        
        if len(self.rate_limit['requests']) >= self.rate_limit['max_requests']:
            self.metrics['rate_limit_hits'] += 1
            logger.warning("Rate limit reached")
            return False
            
        self.rate_limit['requests'].append(current_time)
        return True

    async def _secure_api_call(self, func, *args, **kwargs):
        """Secure API call wrapper with metrics"""
        start_time = time.time()
        
        try:
            # Check rate limit
            if not await self._check_rate_limit():
                wait_time = self.rate_limit['window_size'] - (time.time() - self.rate_limit['requests'][0])
                await asyncio.sleep(wait_time)
            
            # Execute API call
            result = await func(*args, **kwargs)
            
            # Update metrics
            self.metrics['success_count'] += 1
            await self._track_request_time(start_time)
            
            return result
            
        except Exception as e:
            self.metrics['error_count'] += 1
            logger.error(f"API call failed: {e}")
            raise

    def get_performance_metrics(self) -> Dict:
        """Get current performance metrics"""
        return {
            'avg_response_time': sum(self.metrics['response_times']) / len(self.metrics['response_times']) if self.metrics['response_times'] else 0,
            'success_rate': self.metrics['success_count'] / (self.metrics['success_count'] + self.metrics['error_count']) if (self.metrics['success_count'] + self.metrics['error_count']) > 0 else 0,
            'rate_limit_hits': self.metrics['rate_limit_hits'],
            'total_requests': len(self.metrics['response_times'])
        }

    async def secure_token_refresh(self):
        """Secure token refresh implementation"""
        try:
            if os.path.exists(TOKEN_FILE):
                creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
                
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                    
                    # Secure token storage
                    with open(TOKEN_FILE, 'w') as token:
                        token.write(creds.to_json())
                    
                    return True
        except Exception as e:
            logger.error(f"Token refresh failed: {e}")
            return False

    def _update_session_security(self):
        """Update session security settings"""
        st.session_state.security = {
            'last_activity': time.time(),
            'session_duration': 3600,  # 1 hour
            'api_calls_remaining': self.rate_limit['max_requests']
        }

    async def process_song_list(self, songs: List[str], playlist_name: str, 
                          method: str = "ytmusic", privacy: str = "PRIVATE",
                          create_new: bool = True) -> Dict:
        """Process song list with duplicate prevention"""
        songs = list(dict.fromkeys(songs))
        
        results = {
            'playlist_id': None,
            'total_songs': len(songs),
            'successful': 0,
            'failed': [],
            'details': []
        }

        try:
            # Create playlist only if create_new is True
            if create_new:
                if method == "youtube_api":
                    results['playlist_id'] = await self.create_playlist_youtube_api(
                        playlist_name, privacy=privacy.lower()
                    )
                else:
                    results['playlist_id'] = await self.create_playlist_ytmusic(
                        playlist_name, privacy=privacy
                    )

                self.current_playlist_id = results['playlist_id']
            else:
                # Use existing playlist ID
                results['playlist_id'] = self.current_playlist_id

            # Process songs in batches
            for i in range(0, len(songs), self.batch_size):
                batch = songs[i:i + self.batch_size]
                batch_results = await self.process_batch(songs, i)
                
                results['successful'] += batch_results['successful']
                results['failed'].extend(batch_results['failed'])
                results['details'].extend(batch_results['details'])

        except Exception as e:
            logger.error(f"Playlist processing failed: {e}")
            raise

        return results

    async def _process_with_retry(self, song: str) -> Dict:
        """Process a single song with retry logic"""
        result = {
            'success': False,
            'details': {
                'song': song,
                'status': 'Failed',
                'error': None,
                'video_id': None,
                'title': None
            }
        }

        for attempt in range(self.security['max_retries']):
            try:
                # Search for song
                search_results = await self.search_song_youtube_api(song)
                
                if not search_results:
                    result['details']['error'] = 'No search results found'
                    continue

                # Get best match
                best_match = self.get_best_match(search_results, song)
                if not best_match:
                    result['details']['error'] = 'No suitable match found'
                    continue

                # Extract video ID
                video_id = best_match.get('id', {}).get('videoId')
                if not video_id:
                    result['details']['error'] = 'Invalid video ID'
                    continue

                # Add to playlist
                success = await self.add_song_to_playlist_youtube_api(
                    self.current_playlist_id, 
                    video_id
                )

                if success:
                    result.update({
                        'success': True,
                        'details': {
                            'song': song,
                            'status': 'Added',
                            'video_id': video_id,
                            'title': best_match.get('snippet', {}).get('title', 'Unknown'),
                            'error': None
                        }
                    })
                    return result

            except Exception as e:
                result['details']['error'] = str(e)
                await asyncio.sleep(1)  # Wait before retry

        return result

    async def process_with_quota(self, songs: List[str], batch_size: int = 50) -> Dict:
        """Process songs in batches with quota management"""
        results = []
        total_quota = 0
        
        for i in range(0, len(songs), batch_size):
            batch = songs[i:i + batch_size]
            estimated_quota = (len(batch) * (self.quota_settings['search_cost'] + 
                                           self.quota_settings['playlist_insert_cost']))
            
            if total_quota + estimated_quota > self.quota_settings['daily_quota']:
                st.warning(f"⚠️ Daily quota limit approaching. Processed {i} songs.")
                break
                
            batch_results = await self._process_batch(batch)
            results.extend(batch_results)
            total_quota += estimated_quota
            
            # Update progress
            self._update_status(i + len(batch), len(songs))
            
            # Optional: Save progress to allow resuming later
            self._save_progress(results)
            
        return results
        
    def _save_progress(self, results: List[Dict]):
        """Save progress to allow resuming later"""
        with open('transfer_progress.json', 'w') as f:
            json.dump({
                'timestamp': datetime.now().isoformat(),
                'results': results,
                'processed_videos': list(self.processed_videos)
            }, f)
def load_credentials():
    """Load credentials from environment variables"""
    return {
        'web': {
            'client_id': os.getenv('YOUTUBE_CLIENT_ID'),
            'client_secret': os.getenv('YOUTUBE_CLIENT_SECRET'),
            'redirect_uris': ['http://localhost:8080/', 'http://localhost:8501/'],
            'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
            'token_uri': 'https://oauth2.googleapis.com/token'
        }
    }

def authenticate_youtube_api(email: str):
    """Authenticate YouTube Data API with specific email and handle redirect URI"""
    creds = None
    
    # Load existing token
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    
    # If no valid credentials, run OAuth flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None
        
        if not creds:
            credentials_config = load_credentials()
            if not credentials_config:
                return None
            
            # Get available port
            port = get_available_port()
            if not port:
                st.error("❌ No allowed ports available")
                return None
            
            redirect_uri = f"http://localhost:{port}/" # Ensure trailing slash
            
            flow = InstalledAppFlow.from_client_config(
                {"installed": credentials_config}, SCOPES, redirect_uri=redirect_uri
            )
            
            # Configure authorization parameters
            auth_url, _ = flow.authorization_url(
                access_type='offline',
                include_granted_scopes='true',
                login_hint=email,  # Force specific email
                prompt='select_account'  # Force account selection
            )
            
            st.info(f"""
            🔐 **Authentication Details:**
            - Port: {port}
            - Email: {email}
            - URI: {redirect_uri}
            
            Please ensure you:
            1. Sign out of all Google accounts first
            2. Click the authentication link
            3. Select {email} when prompted
            """)
            
            try:
                creds = flow.run_local_server(
                    port=port,
                    success_message='Authentication successful! You may close this window.',
                    authorization_prompt_message=f'Please sign in with {email}',
                    open_browser=True,
                    redirect_uri=redirect_uri # Explicitly pass redirect_uri
                )
                
                # Verify email matches
                if hasattr(creds, 'id_token') and creds.id_token:
                    from google.oauth2.id_token import verify_oauth2_token
                    from google.auth.transport import requests
                    id_info = verify_oauth2_token(creds.id_token, requests.Request())
                    
                    if id_info['email'] != email:
                        st.error(f"❌ Wrong account used! Expected {email} but got {id_info['email']}")
                        return None
                
            except Exception as e:
                logger.error(f"Server error during authentication: {e}")
                st.error(f"❌ Authentication failed: {str(e)}")
                return None
        
        # Save credentials
    """Get OAuth config from Streamlit secrets"""
    return {
        'web': {
            'client_id': st.secrets.oauth.client_id,
            'client_secret': st.secrets.oauth.client_secret,
            'redirect_uris': st.secrets.oauth.redirect_uris,
            'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
            'token_uri': 'https://oauth2.googleapis.com/token',
            'auth_provider_x509_cert_url': 'https://www.googleapis.com/oauth2/v1/certs'
        }
    }

def load_oauth_config():
    """Load OAuth configuration from environment"""
    return {
        'web': {
            'client_id': os.environ.get('YOUTUBE_CLIENT_ID'),
            'client_secret': os.environ.get('YOUTUBE_CLIENT_SECRET'),
            'redirect_uris': os.environ.get('YOUTUBE_REDIRECT_URIS', '').split(','),
            'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
            'token_uri': 'https://oauth2.googleapis.com/token',
            'auth_provider_x509_cert_url': 'https://www.googleapis.com/oauth2/v1/certs'
        }
    }

def setup_authentication():
    """Setup authentication for both services"""
    st.subheader("🔐 Authentication Setup")
    
    # Email configuration
    auth_email = st.text_input(
        "Enter Google Account Email",
        placeholder="your.email@gmail.com",
        help="Enter the Gmail account associated with your Google Cloud Project"
    )
    
    if not auth_email:
        st.warning("⚠️ Please enter your Google account email")
        return False
    
    # Store email in session state
    if 'auth_email' not in st.session_state:
        st.session_state.auth_email = auth_email
    
    # Check credentials file
    creds_config = load_credentials()
    if not creds_config:
        st.error("❌ credentials.json not found!")
        st.markdown("""
        **Setup Steps:**
        1. Go to [Google Cloud Console](https://console.cloud.google.com/)
        2. Sign in with: **{auth_email}**
        3. Enable YouTube Data API v3
        4. Create OAuth 2.0 credentials (Desktop Application)
        5. Download as `credentials.json`
        6. Place in app folder and restart
        """)
        return False
    
    st.success("✅ credentials.json found")
    
    # Show configured redirect URIs
    st.info(f"""
    ⚠️ **Important**: Configure these in Google Cloud Console for {auth_email}:
    
    **Authorized redirect URIs:**
    - `http://localhost:8080/`
    - `http://localhost:8501/`
    
    **Steps:**
    1. Go to Google Cloud Console
    2. Sign in with: **{auth_email}**
    3. Go to: APIs & Services → Credentials → OAuth 2.0 Client
    4. Add both redirect URIs above
    5. Save changes
    """)
    
    # Modify authenticate_youtube_api to use email
    def kill_streamlit_processes():
        """Kill any running Streamlit processes on allowed ports to free them for OAuth."""
        import psutil
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info['cmdline']
                if cmdline and any('streamlit' in arg for arg in cmdline):
                    for port in ALLOWED_PORTS:
                        if any(str(port) in arg for arg in cmdline):
                            proc.kill()
            except Exception:
                continue

    def authenticate_youtube_api(email: str):
        """Authenticate YouTube Data API with specific email"""
        try:
            # Kill existing processes first
            kill_streamlit_processes()
            
            # Get available port
            port = get_available_port()
            if not port:
                st.error("❌ No allowed ports available")
                return None
                
            # Load credentials
            if not os.path.exists(CREDENTIALS_FILE):
                st.error("❌ credentials.json not found")
                return None
                
            with open(CREDENTIALS_FILE, 'r') as f:
                client_config = json.load(f)
            
            # Create flow with specific configuration
            flow = InstalledAppFlow.from_client_config(
                client_config,
                SCOPES,
                redirect_uri=f"http://localhost:{port}"
            )
            
            # Configure authorization parameters
            auth_url, _ = flow.authorization_url(
                access_type='offline',
                include_granted_scopes='true',
                login_hint=email,  # Force specific email
                prompt='select_account'  # Force account selection
            )
            
            # Show authentication info
            st.info(f"""
            🔐 **Authentication Details:**
            - Port: {port}
            - Email: {email}
            - URI: http://localhost:{port}
            
            Please ensure you:
            1. Sign out of all Google accounts first
            2. Click the authentication link
            3. Select {email} when prompted
            """)
            
            try:
                # Run local server with specific port
                creds = flow.run_local_server(
                    port=port,
                    success_message='Authentication successful! You may close this window.',
                    authorization_prompt_message=f'Please sign in with {email}',
                    open_browser=True
                )
                
                # Verify email matches
                if hasattr(creds, 'id_token') and creds.id_token:
                    from google.oauth2.id_token import verify_oauth2_token
                    from google.auth.transport import requests
                    id_info = verify_oauth2_token(creds.id_token, requests.Request())
                    
                    if id_info['email'] != email:
                        st.error(f"❌ Wrong account used! Expected {email} but got {id_info['email']}")
                        return None
                
                # Save credentials if email matches
                with open(TOKEN_FILE, 'w') as token:
                    token.write(creds.to_json())
                
                return build('youtube', 'v3', credentials=creds)
                
            except Exception as e:
                logger.error(f"Server error: {e}")
                st.error(f"❌ Authentication failed: {str(e)}")
                return None
                
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            st.error(f"❌ Authentication failed: {str(e)}")
            return None
    
    # Authentication method selection
    auth_method = st.radio(
        "Choose authentication method:",
        [
            "🔗 YouTube Data API (OAuth)",
            "🎵 YTMusic API (OAuth)",
            "🔑 API Key"
        ]
    )
    
    if auth_method == "🔗 YouTube Data API (OAuth)":
        if st.button("🚀 Authenticate YouTube API", type="primary"):
            try:
                with st.spinner(f"Authenticating {auth_email}..."):
                    # Clear existing tokens
                    if os.path.exists(TOKEN_FILE):
                        os.remove(TOKEN_FILE)
                    
                    youtube_service = authenticate_youtube_api(auth_email)
                    if youtube_service:
                        st.session_state.youtube_service = youtube_service
                        st.session_state.auth_status = True
                        st.session_state.current_account = f"YouTube API OAuth ({auth_email})"
                        st.success(f"✅ Authenticated with {auth_email}")
                        st.balloons()
                        time.sleep(2)
                        st.rerun()
                    else:
                        st.error("❌ Authentication failed")
            except Exception as e:
                st.error(f"❌ Authentication error: {str(e)}")
    
    elif auth_method == "🎵 YTMusic API (OAuth)":

        def authenticate_ytmusic():
            """Authenticate YTMusic using browser-based OAuth and save credentials."""
            try:
                # Use browser-based OAuth for YTMusic
                if os.path.exists(YTMUSIC_AUTH_FILE):
                    return YTMusic(YTMUSIC_AUTH_FILE)
                else:
                    # This will open a browser for the user to authenticate and save the headers
                    YTMusic.setup(filepath=YTMUSIC_AUTH_FILE)
                    return YTMusic(YTMUSIC_AUTH_FILE)
            except Exception as e:
                logger.error(f"YTMusic authentication failed: {e}")
                return None

        if st.button("🚀 Authenticate YTMusic", type="primary"):
            try:
                with st.spinner("Authenticating... Browser will open for authorization."):
                    ytmusic = authenticate_ytmusic()
                    
                    if ytmusic:
                        st.session_state.ytmusic = ytmusic
                        st.session_state.auth_status = True
                        st.session_state.current_account = "YTMusic OAuth"
                        
                        st.success("✅ YTMusic authenticated!")
                        st.balloons()
                        st.rerun()
                    else:
                        st.error("❌ Authentication failed")
            except Exception as e:
                st.error(f"❌ Authentication error: {e}")
                if "redirect_uri_mismatch" in str(e):
                    st.error("""
                    **Redirect URI Mismatch Error:**
                    Please add the redirect URI shown in the error to your Google Cloud Console OAuth client.
                    """)
    
    else:  # API Key method
        api_key = os.environ.get('YOUTUBE_API_KEY')
        if api_key:
            st.info(f"✅ API Key found: {api_key[:10]}...")
            
            if st.button("🔑 Initialize with API Key", type="primary"):
                try:
                    with st.spinner("Testing API Key..."):
                        youtube_service = build('youtube', 'v3', developerKey=api_key)
                        
                        # Test the API key
                        youtube_service.search().list(
                            q='test',
                            part='snippet',
                            type='video',
                            maxResults=1
                        ).execute()
                        
                        st.session_state.youtube_service = youtube_service
                        st.session_state.api_key = api_key
                        st.session_state.auth_status = True
                        st.session_state.current_account = "API Key"
                        
                        st.success("✅ API Key authenticated!")
                        st.balloons()
                        st.rerun()
                except Exception as e:
                    st.error(f"❌ API Key test failed: {e}")
        else:
            st.error("❌ YOUTUBE_API_KEY not found in environment variables")
            st.info("Set the environment variable: YOUTUBE_API_KEY=your_api_key")
    
    return False

def detect_song_column(df):
    """Detect song column in CSV"""
    possible_names = [
        'song', 'songs', 'title', 'track', 'name', 'music',
        'song name', 'track name', 'song title', 'spotify'
    ]
    
    for col in df.columns:
        if col.lower().strip() in possible_names:
            return col
    
    for col in df.columns():
        col_lower = col.lower().strip()
        for name in possible_names:
            if name in col_lower or col_lower in name:
                return col
    
    return None

def test_youtube_api_connection(youtube):
    """Test YouTube API connection with proper parameters"""
    try:
        # Test with search instead of playlists
        response = youtube.search().list(
            part="snippet",
            maxResults=1,
            q="test",
            type="video"
        ).execute()
        return True if response else False
    except Exception as e:
        logger.error(f"API test failed: {e}")
        return False

def check_youtube_api_status():
    """Check YouTube API status and configuration"""
    try:
        # 1. Check API Key
        api_key = os.environ.get('YOUTUBE_API_KEY')
        if not api_key:
            return "❌ API Key not found in environment variables"
            
        # 2. Check credentials file
        if not os.path.exists(CREDENTIALS_FILE):
            return "❌ credentials.json not found"
            
        # 3. Check YouTube API enabled
        youtube = build('youtube', 'v3', developerKey=api_key)
        try:
            youtube.search().list(
                part="snippet",
                maxResults=1,
                q="test"
            ).execute()
        except Exception as e:
            return f"❌ YouTube API not enabled or invalid: {str(e)}"
            
        return "✅ YouTube API configuration OK"
        
    except Exception as e:
        return f"❌ Configuration check failed: {str(e)}"

def load_config():
    """Load configuration from file"""
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    if not os.path.exists(config_path):
        return None
    
    with open(config_path, 'r') as f:
        return json.load(f)

def main():
    st.title("🎵 YouTube Music Automation Agent")

    # Initialize session state for file upload
    if 'uploaded_file' not in st.session_state:
        st.session_state.uploaded_file = None

    # Sidebar
    def render_sidebar():
        """Render optimized sidebar with metrics and quotas"""
        with st.sidebar:
            # Collapsible Authentication Status
            with st.expander("🔐 Auth Status", expanded=False):
                if st.session_state.auth_status:
                    st.success("✅ Authenticated")
                    st.caption(f"**{st.session_state.current_account}**")
                else:
                    st.error("❌ Not Authenticated")

            # Compact Statistics
            with st.expander("📊 Quick Stats", expanded=True):
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("🔍 Searches", st.session_state.stats['searches'], delta=None, help="Total searches performed")
                    st.metric("📝 Playlists", st.session_state.stats['created_playlists'], delta=None, help="Playlists created")
                with col2:
                    st.metric("✅ Added", st.session_state.stats['added'], delta=None, help="Songs successfully added")
                    st.metric("❌ Errors", st.session_state.stats['errors'], delta=None, help="Failed operations")

            # API Quota Usage
            with st.expander("📈 API Usage", expanded=True):
                quota_used = (st.session_state.stats['searches'] * YOUTUBE_API_QUOTAS['search'] +
                            st.session_state.stats['added'] * YOUTUBE_API_QUOTAS['playlist_insert'])
                quota_percent = (quota_used / YOUTUBE_API_QUOTAS['daily_limit']) * 100
                
                st.progress(quota_percent / 100, text=f"Quota: {quota_percent:.1f}%")
                st.caption(f"Used: {quota_used:,} / {YOUTUBE_API_QUOTAS['daily_limit']:,} units")
                
                # Cost estimation (assuming $5 per 1M units)
                estimated_cost = (quota_used / 1_000_000) * 5
                st.caption(f"Est. Cost: ${estimated_cost:.4f}")

            # System Info
            with st.expander("ℹ️ System Info", expanded=False):
                st.caption("**Dependencies:**")
                for lib, version in DEPENDENCIES.items():
                    st.caption(f"- {lib}: {version}")
                
                st.caption("**Limits:**")
                st.caption(f"- Max songs/playlist: {YOUTUBE_API_QUOTAS['playlist_item_limit']:,}")
                st.caption(f"- Daily API limit: {YOUTUBE_API_QUOTAS['daily_limit']:,}")

            # Reset Stats Button
            if st.button("🔄 Reset Stats", use_container_width=True):
                st.session_state.stats = {
                    'searches': 0, 
                    'added': 0, 
                    'errors': 0, 
                    'created_playlists': 0
                }
                st.rerun()
    
    render_sidebar()
    
    # Authentication check
    if not st.session_state.auth_status:
        setup_authentication()
        return
    
    st.success(f"✅ Authenticated with {st.session_state.current_account}")
    
    # Add security monitoring
    if 'security' not in st.session_state:
        st.session_state.security = {
            'last_activity': time.time(),
            'session_duration': 3600,
            'api_calls_remaining': 60
        }

    # Security sidebar
    with st.sidebar:
        st.subheader("🔒 Security Status")
        
        # Session timeout warning
        session_time = time.time() - st.session_state.security['last_activity']
        if session_time > st.session_state.security['session_duration']:
            st.warning("⚠️ Session expired. Please re-authenticate.")
            
        # API quota status
        st.metric("API Calls Remaining", 
                 st.session_state.security['api_calls_remaining'])
    
    # Main interface
    col1, col2 = st.columns([3, 2])
    
    with col1:
        st.subheader("📁 Upload Song List")
        uploaded_file = st.file_uploader(
            "Upload CSV file (Spotify export or song list)",
            type=['csv'],
            help="CSV file with song names from Spotify or other sources"
        )
        
        # Example CSV download
        if st.button("📥 Download Example CSV"):
            example_df = pd.DataFrame({
                'Song': [
                    'Bohemian Rhapsody - Queen',
                    'Hotel California - Eagles',
                    'Imagine - John Lennon',
                    'Stairway to Heaven - Led Zeppelin',
                    'Sweet Child O Mine - Guns N Roses'
                ]
            })
            csv_data = example_df.to_csv(index=False)
            st.download_button(
                "📄 Download Example",
                data=csv_data,
                file_name="example_spotify_songs.csv",
                mime="text/csv"
            )
    
    with col2:
        st.subheader("⚙️ Configuration")
        
        playlist_name = st.text_input(
            "Playlist name:",
            value=f"Spotify Transfer {datetime.now().strftime('%m-%d')}"
        )
        
        # Method selection based on available authentication
        available_methods = []
        if st.session_state.youtube_service:
            if st.session_state.current_account == "API Key":
                available_methods.append("YouTube API (Search Only)")
            else:
                available_methods.append("YouTube API (Full)")
        if st.session_state.ytmusic:
            available_methods.append("YTMusic API")
        
        if available_methods:
            method = st.selectbox("Processing method:", available_methods)
            
            # Convert method selection to internal format
            if "YouTube API" in method:
                internal_method = "youtube_api"
            else:
                internal_method = "ytmusic"
        else:
            st.error("No authenticated services available")
            return
        
        privacy = st.selectbox(
            "Privacy:",
            ["PRIVATE", "UNLISTED", "PUBLIC"],
            index=0
        )
        
        delay = st.slider(
            "Delay (seconds):",
            min_value=0.5,
            max_value=3.0,
            value=1.0,
            step=0.5,
            help="Delay between API calls to avoid rate limiting"
        )
    
    # Process CSV
    if uploaded_file:
        try:
            # Read CSV
            df = pd.read_csv(uploaded_file)
            st.success(f"✅ Loaded CSV with {len(df)} rows")
            
            # Show preview
            with st.expander("👀 CSV Preview"):
                st.dataframe(df.head(), use_container_width=True)
            
            # Detect song column
            song_col = detect_song_column(df)
            
            if not song_col:
                st.error("❌ Could not detect song column")
                song_col = st.selectbox(
                    "Select song column:",
                    df.columns.tolist()
                )
            else:
                st.success(f"✅ Detected song column: '{song_col}'")
            
            if song_col:
                # Clean data
                songs = df[song_col].dropna().astype(str).str.strip().tolist()
                songs = [s for s in songs if s and s.lower() not in ['nan', 'none', '']]
                
                st.info(f"📋 Found {len(songs)} valid songs")
                
                # Preview songs
                with st.expander("🎵 Songs to Process"):
                    preview_df = pd.DataFrame({'Song': songs[:15]})
                    st.dataframe(preview_df, use_container_width=True)
                    if len(songs) > 15:
                        st.info(f"... and {len(songs) - 15} more songs")
                
                # Process button
                if st.button("🚀 Create YouTube Music Playlist", type="primary", use_container_width=True):
                    if not playlist_name.strip():
                        st.warning("⚠️ Please enter a playlist name")
                        return
                    
                    # Check if method is available for playlist creation
                    if internal_method == "youtube_api" and st.session_state.current_account == "API Key":
                        st.error("❌ API Key method only supports search. Use OAuth for playlist creation.")
                        return
                    
                    # Initialize automation agent
                    agent = YouTubeMusicAutomationAgent()
                    agent.batch_size = st.slider("Batch Size", 10, 100, 50)
                    agent.max_retries = st.slider("Max Retries", 1, 5, 3)
                    agent.rate_limit['requests_per_minute'] = st.slider("Requests per Minute", 30, 120, 60)
                    agent.set_services(st.session_state.youtube_service, st.session_state.ytmusic)
                    agent.set_delay(delay)
                    if st.session_state.api_key:
                        agent.set_api_key(st.session_state.api_key)
                    
                    # Process playlist
                    try:
                        with st.spinner(f"🎵 Creating '{playlist_name}' using {internal_method.upper()}..."):
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            results = loop.run_until_complete(
                                agent.process_song_list(songs, playlist_name, internal_method, privacy)
                            )
                        
                        # Show results
                        st.markdown("---")
                        st.subheader("🎉 Playlist Transfer Complete!")
                        
                        # Metrics
                        col1, col2, col3, col4 = st.columns(4)
                        with col1:
                            st.metric("📊 Total", results['total_songs'])
                        with col2:
                            st.metric("✅ Added", results['successful'])
                        with col3:
                            st.metric("❌ Failed", len(results['failed']))
                        with col4:
                            success_rate = (results['successful'] / results['total_songs'] * 100) if results['total_songs'] > 0 else 0
                            st.metric("📈 Success", f"{success_rate:.1f}%")
                        
                        # Progress bar
                        if results['total_songs'] > 0:
                            st.progress(results['successful'] / results['total_songs'])
                        
                        # Playlist link
                        if results['playlist_id']:
                            playlist_url = f"https://music.youtube.com/playlist?list={results['playlist_id']}"
                            st.success(f"🎵 **[🔗 Open Playlist in YouTube Music]({playlist_url})**")
                        
                        # Download results
                        if results['details']:
                            results_df = pd.DataFrame(results['details'])
                            csv_data = results_df.to_csv(index=False)
                            
                            col1, col2 = st.columns(2)
                            with col1:
                                st.download_button(
                                    "📄 Download Transfer Report",
                                    data=csv_data,
                                    file_name=f"spotify_transfer_report_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                                    mime="text/csv"
                                )
                            
                            with col2:
                                if results['failed']:
                                    failed_df = pd.DataFrame({'Failed Songs': results['failed']})
                                    failed_csv = failed_df.to_csv(index=False)
                                    st.download_button(
                                        "❌ Download Failed Songs",
                                        data=failed_csv,
                                        file_name=f"failed_songs_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                                        mime="text/csv"
                                    )
                        
                        # Show detailed results
                        with st.expander("📊 Detailed Transfer Results"):
                            if results['details']:
                                st.dataframe(pd.DataFrame(results['details']), use_container_width=True)
                        
                        # Show failed songs
                        if results['failed']:
                            with st.expander(f"❌ Failed Songs ({len(results['failed'])})"):
                                st.write("**Songs that could not be found or added:**")
                                failed_df = pd.DataFrame({'Song': results['failed']})
                                st.dataframe(failed_df, use_container_width=True)
                    
                    except Exception as e:
                        st.error(f"❌ Playlist creation failed: {e}")
                        st.info("💡 Try using a different method or check your authentication")
                        logger.error(f"Playlist creation error: {traceback.format_exc()}")
        
        except Exception as e:
            st.error(f"❌ Error reading CSV: {e}")
    
    # Quick actions
    st.markdown("---")
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("🧪 Test Connection"):
            try:
                if st.session_state.youtube_service:
                    st.session_state.youtube_service.search().list(
                        q='test', part='snippet', type='video', maxResults=1
                    ).execute()
                    st.success("✅ YouTube API working!")
                elif st.session_state.ytmusic:
                    st.session_state.ytmusic.get_home()
                    st.success("✅ YTMusic working!")
            except Exception as e:
                st.error(f"❌ Connection failed: {e}")
    
    with col2:
        if st.button("🔄 Re-authenticate"):
            # Clear authentication
            for file in [TOKEN_FILE, YTMUSIC_AUTH_FILE]:
                if os.path.exists(file):
                    os.remove(file)
            
            st.session_state.auth_status = False
            st.session_state.current_account = None
            st.session_state.youtube_service = None
            st.session_state.ytmusic = None
            st.rerun()
    
    with col3:
        if st.button("📚 View Documentation"):
            with st.expander("📖 How to Use"):
                st.markdown("""
                ## 🎯 Quick Start Guide
                
                ### 1. Authentication
                - **OAuth (Recommended)**: Full playlist creation capabilities
                - **API Key**: Search-only, requires OAuth for playlist creation
                
                ### 2. CSV Format
                - Any CSV with song names in a column
                - Supports Spotify exports
                - Auto-detects song columns
                
                ### 3. Processing
                - Choose YouTube API or YTMusic API
                - Configure privacy and delay settings
                - Monitor real-time progress
                
                ### 4. Results
                - Download transfer reports
                - Review failed songs
                - Access playlist directly
                """)
    
    # Debugging section

    def is_port_in_use(port):
        """Check if a port is in use on localhost."""
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('localhost', port)) == 0

    if st.button("🔍 Debug Authentication"):
        status = check_youtube_api_status()
        st.info(status)
        
        if os.path.exists(CREDENTIALS_FILE):
            with open(CREDENTIALS_FILE, 'r') as f:
                creds = json.load(f)  # Load credentials as JSON
            st.json(creds)  # Show credentials structure
            
        st.write("Environment Variables:")
        st.write({
            "API_KEY": os.environ.get('YOUTUBE_API_KEY', 'Not Set'),
            "PORT_8501": "In Use" if is_port_in_use(8501) else "Available",
            "PORT_8080": "In Use" if is_port_in_use(8080) else "Available"
        })

def check_port_available(port: int) -> bool:
    """Check if specific port is available"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(('localhost', port))
        sock.close()
        return True
    except OSError:
        sock.close()
        return False

def get_available_port() -> Optional[int]:
    """Get first available port from allowed ports"""
    for port in ALLOWED_PORTS:
        if check_port_available(port):
            logger.info(f"Found available port: {port}")
            return port
    logger.error("No allowed ports available")
    return None

# Add this function to support adding to existing playlists
async def add_to_existing_playlist(agent, songs: List[str]):
    """Add songs to an existing playlist"""
    try:
        # Get user's playlists
        playlists_request = agent.youtube_service.playlists().list(
            part="snippet,contentDetails",
            mine=True,
            maxResults=50
        )
        playlists = playlists_request.execute()

        if not playlists.get('items'):
            st.warning("No playlists found. Create a playlist first.")
            return None

        # Create playlist selection with current song count
        playlist_options = {}
        for p in playlists['items']:
            song_count = p['contentDetails']['itemCount']
            title = p['snippet']['title']
            playlist_id = p['id']
            display_name = f"{title} ({song_count} songs)"
            playlist_options[display_name] = {
                'id': playlist_id,
                'title': title,
                'song_count': song_count
            }

        st.subheader("Select Playlist to Add Songs")
        selected = st.selectbox(
            "Choose existing playlist:",
            options=list(playlist_options.keys())
        )

        if selected and songs:
            playlist_info = playlist_options[selected]
            playlist_id = playlist_info['id']
            current_size = playlist_info['song_count']
            
            # Show playlist details
            st.info(f"""
            📝 **Playlist Details:**
            - Name: {playlist_info['title']}
            - Current Songs: {current_size}
            - Songs to Add: {len(songs)}
            """)

            # Check playlist size limit
            remaining_space = YOUTUBE_API_QUOTAS['playlist_item_limit'] - current_size
            if len(songs) > remaining_space:
                st.warning(f"⚠️ Can only add {remaining_space} more songs (playlist limit: {YOUTUBE_API_QUOTAS['playlist_item_limit']})")
                songs = songs[:remaining_space]

            if st.button("✅ Add Songs to Existing Playlist", type="primary"):
                with st.spinner(f"Adding {len(songs)} songs to '{playlist_info['title']}'..."):
                    # Set the playlist ID in agent
                    agent.current_playlist_id = playlist_id
                    
                    # Process songs
                    results = await agent.process_song_list(
                        songs=songs,
                        playlist_name=playlist_info['title'],
                        method="youtube_api",
                        create_new=False  # Flag to prevent new playlist creation
                    )

                    if results['successful'] > 0:
                        st.success(f"✅ Added {results['successful']} songs to '{playlist_info['title']}'")
                        
                        # Show playlist link
                        playlist_url = f"https://music.youtube.com/playlist?list={playlist_id}"
                        st.markdown(f"🔗 [Open Updated Playlist]({playlist_url})")
                        
                        # Show failed songs if any
                        if results['failed']:
                            with st.expander(f"❌ Failed Songs ({len(results['failed'])})"):
                                for song in results['failed']:
                                    st.write(f"- {song}")
                    else:
                        st.error("❌ Failed to add songs. Please try again.")

            return results

    except Exception as e:
        st.error(f"Error accessing playlists: {str(e)}")
        logger.error(f"Playlist access error: {traceback.format_exc()}")
        return None

# Add this function to show and handle existing playlists
async def handle_existing_playlists(agent, songs: List[str]):
    """Handle adding songs to existing playlists for both YouTube API and YTMusic"""
    try:
        playlists = []
        
        # Get playlists based on available service
        if agent.youtube_service:
            # Get YouTube API playlists
            response = agent.youtube_service.playlists().list(
                part="snippet,contentDetails",
                mine=True,
                maxResults=50
            ).execute()
            playlists.extend([{
                'id': p['id'],
                'title': p['snippet']['title'],
                'song_count': p['contentDetails']['itemCount'],
                'service': 'youtube'
            } for p in response.get('items', [])])
            
        if agent.ytmusic:
            # Get YTMusic playlists
            ytmusic_playlists = agent.ytmusic.get_library_playlists(limit=50)
            playlists.extend([{
                'id': p['playlistId'],
                'title': p['title'],
                'song_count': p.get('count', 0),
                'service': 'ytmusic'
            } for p in ytmusic_playlists])

        if not playlists:
            st.warning("No playlists found. Create a playlist first.")
            return None

        # Create selection interface
        st.subheader("📝 Select Existing Playlist")
        
        # Group playlists by service
        youtube_playlists = [p for p in playlists if p['service'] == 'youtube']
        ytmusic_playlists = [p for p in playlists if p['service'] == 'ytmusic']
        
        service = st.radio(
            "Choose Service:",
            ["YouTube Music", "YouTube"],
            horizontal=True
        )
        
        playlist_options = {}
        display_playlists = ytmusic_playlists if service == "YouTube Music" else youtube_playlists
        
        for p in display_playlists:
            display_name = f"{p['title']} ({p['song_count']} songs)"
            playlist_options[display_name] = p

        if playlist_options:
            selected = st.selectbox(
                "Select playlist to add songs:",
                options=list(playlist_options.keys())
            )

            if selected and songs:
                playlist_info = playlist_options[selected]
                
                # Show playlist details
                st.info(f"""
                📝 **Playlist Details:**
                - Name: {playlist_info['title']}
                - Current Songs: {playlist_info['song_count']}
                - Songs to Add: {len(songs)}
                - Service: {playlist_info['service']}
                """)

                # Check size limits
                remaining_space = YOUTUBE_API_QUOTAS['playlist_item_limit'] - playlist_info['song_count']
                if len(songs) > remaining_space:
                    st.warning(f"⚠️ Can only add {remaining_space} more songs (limit: {YOUTUBE_API_QUOTAS['playlist_item_limit']})")
                    songs = songs[:remaining_space]

                if st.button("➕ Add Songs to Playlist", type="primary"):
                    with st.spinner(f"Adding {len(songs)} songs to '{playlist_info['title']}'..."):
                        agent.current_playlist_id = playlist_info['id']
                        
                        # Process songs using appropriate service
                        method = "ytmusic" if playlist_info['service'] == 'ytmusic' else "youtube_api"
                        results = await agent.process_song_list(
                            songs=songs,
                            playlist_name=playlist_info['title'],
                            method=method,
                            create_new=False
                        )

                        if results['successful'] > 0:
                            st.success(f"✅ Added {results['successful']} songs!")
                            
                            # Show playlist link
                            base_url = "https://music.youtube.com" if method == "ytmusic" else "https://www.youtube.com"
                            playlist_url = f"{base_url}/playlist?list={playlist_info['id']}"
                            st.markdown(f"🔗 [Open Updated Playlist]({playlist_url})")
                            
                            # Show failed songs
                            if results['failed']:
                                with st.expander(f"❌ Failed Songs ({len(results['failed'])})"):
                                    for song in results['failed']:
                                        st.write(f"- {song}")
                        else:
                            st.error("❌ Failed to add songs. Please try again.")
        else:
            st.info(f"No playlists found for {service}")

    except Exception as e:
        st.error(f"Error accessing playlists: {str(e)}")
        logger.error(f"Playlist access error: {traceback.format_exc()}")
        return None

def setup_ytmusic() -> Optional[YTMusic]:
    """Setup YTMusic with proper OAuth handling"""
    try:
        # Check for existing OAuth token
        if os.path.exists(YTMUSIC_AUTH_FILE):
            try:
                ytmusic = YTMusic(YTMUSIC_AUTH_FILE)
                # Test the connection
                ytmusic.get_library_playlists(limit=1)
                logger.info("✅ YTMusic authenticated using existing token")
                return ytmusic
            except Exception as e:
                logger.warning(f"Existing YTMusic token failed: {e}")
                # Delete invalid token file
                os.remove(YTMUSIC_AUTH_FILE)
        
        # Setup new OAuth
        if os.path.exists(CREDENTIALS_FILE):
            try:
                # Use same credentials as YouTube API
                with open(CREDENTIALS_FILE, 'r') as f:
                    creds_data = json.load(f)
                
                # Setup OAuth
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': '*/*',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Content-Type': 'application/json'
                }
                
                ytmusic = YTMusic(
                    auth=YTMUSIC_AUTH_FILE,
                    headers=headers,
                    oauth_credentials=creds_data['web']
                )
                
                # Test connection
                ytmusic.get_library_playlists(limit=1)
                logger.info("✅ YTMusic authenticated with new OAuth token")
                return ytmusic
                
            except Exception as e:
                logger.error(f"❌ YTMusic OAuth setup failed: {e}")
                st.error("""
                Failed to authenticate with YTMusic. Please try:
                1. Re-authenticating using the "Re-authenticate" button
                2. Checking your internet connection
                3. Ensuring your Google account has access to YouTube Music
                """)
                return None
        else:
            logger.error("❌ No credentials.json file found")
            st.error("Please ensure you have credentials.json file in the project directory")
            return None
            
    except Exception as e:
        logger.error(f"❌ YTMusic setup failed: {e}")
        st.error(f"YTMusic authentication error: {str(e)}")
        return None

if __name__ == "__main__":
    main()

    # Initialize YTMusic
    if 'ytmusic' not in st.session_state:
        ytmusic = setup_ytmusic()
        if ytmusic:
            st.session_state.ytmusic = ytmusic
            st.success("✅ Connected to YouTube Music")
        else:
            st.warning("⚠️ YTMusic connection failed")
    
    # Add re-authentication button
    if st.button("🔄 Re-authenticate YTMusic"):
        if os.path.exists(YTMUSIC_AUTH_FILE):
            os.remove(YTMUSIC_AUTH_FILE)
        st.session_state.pop('ytmusic', None)
        st.rerun()
