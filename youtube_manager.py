import streamlit as st
import pandas as pd
import os
import logging
import asyncio
import json
from datetime import datetime
from typing import Dict, List, Optional
import unicodedata
from ytmusicapi import YTMusic
import shutil
from pathlib import Path
import traceback
import sys
import tempfile
import webbrowser
import threading
import time
import socket
from urllib.parse import urlparse, parse_qs

# Import authentication methods
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
import google.auth.exceptions

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s'
)
logger = logging.getLogger(__name__)

# OAuth 2.0 scopes for YouTube Music
SCOPES = [
    'https://www.googleapis.com/auth/youtube',
    'https://www.googleapis.com/auth/youtube.force-ssl',
    'https://www.googleapis.com/auth/youtube.readonly'
]

# Credentials file path
CREDENTIALS_FILE = 'credentials.json'
TOKEN_FILE = 'token.json'

# Page config
st.set_page_config(
    page_title="🎵 YouTube Music Playlist Creator",
    page_icon="🎵",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize session state
if 'ytmusic' not in st.session_state:
    st.session_state.ytmusic = None
if 'auth_status' not in st.session_state:
    st.session_state.auth_status = False
if 'current_account' not in st.session_state:
    st.session_state.current_account = None
if 'credentials' not in st.session_state:
    st.session_state.credentials = None
if 'transfer_results' not in st.session_state:
    st.session_state.transfer_results = None
if 'token_usage' not in st.session_state:
    st.session_state.token_usage = {
        'total_tokens': 0,
        'search_queries': 0,
        'api_calls': 0
    }
if 'oauth_flow' not in st.session_state:
    st.session_state.oauth_flow = None
if 'auth_url' not in st.session_state:
    st.session_state.auth_url = None

class RateLimiter:
    def __init__(self, calls_per_second: int = 2):
        self.calls_per_second = calls_per_second
        self.minimum_interval = 1.0 / calls_per_second
        self.last_call_time = 0

    async def wait(self):
        current_time = asyncio.get_event_loop().time()
        time_since_last_call = current_time - self.last_call_time
        if time_since_last_call < self.minimum_interval:
            await asyncio.sleep(self.minimum_interval - time_since_last_call)
        self.last_call_time = current_time

class YouTubeMusicHandler:
    def __init__(self):
        self.ytmusic = None
        self.rate_limiter = RateLimiter(2)

    def set_ytmusic(self, ytmusic):
        self.ytmusic = ytmusic

    def _sanitize_text(self, text: str) -> str:
        try:
            if not text:
                return ""
            if isinstance(text, bytes):
                text = text.decode('utf-8', errors='ignore')
            
            normalized = unicodedata.normalize('NFKC', str(text))
            sanitized = ''.join(char for char in normalized 
                              if char.isprintable() or char.isspace())
            
            special_chars = {'–': '-', '—': '-', ''': "'", ''': "'", '"': '"', '"': '"'}
            for old, new in special_chars.items():
                sanitized = sanitized.replace(old, new)
            
            return sanitized.strip()
        except Exception as e:
            logger.error(f"Text sanitization failed: {e}")
            return str(text)

    async def create_playlist(self, name: str, description: str = "", privacy: str = "PRIVATE") -> str:
        await self.rate_limiter.wait()
        try:
            sanitized_name = self._sanitize_text(name)
            sanitized_desc = self._sanitize_text(description)
            playlist_id = self.ytmusic.create_playlist(
                sanitized_name, 
                sanitized_desc,
                privacy_status=privacy
            )
            logger.info(f"Created playlist: {sanitized_name}")
            st.session_state.token_usage['api_calls'] += 1
            return playlist_id
        except Exception as e:
            logger.error(f"Playlist creation failed: {e}")
            raise

    async def search_song(self, query: str) -> Optional[List[Dict]]:
        await self.rate_limiter.wait()
        try:
            sanitized_query = self._sanitize_text(query)
            results = self.ytmusic.search(sanitized_query, filter="songs")
            st.session_state.token_usage['search_queries'] += 1
            st.session_state.token_usage['api_calls'] += 1
            st.session_state.token_usage['total_tokens'] += 50
            return results[:3] if results else None
        except Exception as e:
            logger.error(f"Search failed for '{query}': {e}")
            return None

    async def add_to_playlist(self, playlist_id: str, video_id: str) -> bool:
        await self.rate_limiter.wait()
        try:
            self.ytmusic.add_playlist_items(playlist_id, [video_id])
            st.session_state.token_usage['api_calls'] += 1
            return True
        except Exception as e:
            logger.error(f"Failed to add song {video_id}: {e}")
            raise

def find_free_port(start_port=8080, max_port=8090):
    """Find a free port starting from start_port"""
    for port in range(start_port, max_port + 1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('localhost', port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"No free port found in range {start_port}-{max_port}")

def get_chrome_logged_in_accounts():
    """Get logged-in Google accounts from Chrome"""
    try:
        accounts = []
        system = os.name
        
        if system == 'nt':  # Windows
            chrome_user_data = os.path.expanduser(r"~\AppData\Local\Google\Chrome\User Data")
        elif system == 'posix':
            if sys.platform == 'darwin':  # macOS
                chrome_user_data = os.path.expanduser("~/Library/Application Support/Google/Chrome")
            else:  # Linux
                chrome_user_data = os.path.expanduser("~/.config/google-chrome")
        else:
            return accounts
        
        if not os.path.exists(chrome_user_data):
            return accounts
        
        for item in os.listdir(chrome_user_data):
            profile_path = os.path.join(chrome_user_data, item)
            
            if os.path.isdir(profile_path) and (item.startswith("Profile") or item == "Default"):
                prefs_file = os.path.join(profile_path, "Preferences")
                if os.path.exists(prefs_file):
                    try:
                        with open(prefs_file, 'r', encoding='utf-8') as f:
                            prefs = json.load(f)
                        
                        # Look for Google account info
                        if 'account_info' in prefs:
                            for account in prefs['account_info']:
                                if 'email' in account:
                                    accounts.append({
                                        'email': account['email'],
                                        'name': account.get('full_name', account['email'].split('@')[0]),
                                        'profile': item
                                    })
                        
                        # Alternative: look in signin section
                        elif 'signin' in prefs and 'allowed_username' in prefs['signin']:
                            email = prefs['signin']['allowed_username']
                            if email:
                                accounts.append({
                                    'email': email,
                                    'name': email.split('@')[0],
                                    'profile': item
                                })
                    except:
                        continue
        
        # Remove duplicates
        unique_accounts = {}
        for account in accounts:
            email = account['email']
            if email not in unique_accounts:
                unique_accounts[email] = account
        
        return list(unique_accounts.values())
    except Exception as e:
        logger.error(f"Error getting Chrome accounts: {e}")
        return []

def load_credentials():
    """Load credentials from file"""
    try:
        if os.path.exists(TOKEN_FILE):
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
            return creds
        return None
    except Exception as e:
        st.error(f"Error loading credentials: {e}")
        return None

def save_credentials(creds):
    """Save credentials to file"""
    try:
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    except Exception as e:
        st.error(f"Error saving credentials: {e}")

def setup_oauth_flow():
    """Setup OAuth flow for Google authentication"""
    try:
        if not os.path.exists(CREDENTIALS_FILE):
            st.error(f"❌ {CREDENTIALS_FILE} not found in the app directory")
            st.info("""
            Please ensure you have:
            1. Downloaded your OAuth credentials from Google Cloud Console
            2. Renamed the file to 'credentials.json'
            3. Placed it in the same directory as this app
            """)
            return None
        
        # Create OAuth flow from credentials file
        flow = InstalledAppFlow.from_client_secrets_file(
            CREDENTIALS_FILE,
            SCOPES
        )
        
        return flow
    except Exception as e:
        st.error(f"Failed to setup OAuth flow: {e}")
        st.error("Please check your credentials.json file format")
        return None

def authenticate_google_manual():
    """Manual OAuth flow with account selection"""
    try:
        # Start new authentication flow
        flow = setup_oauth_flow()
        if not flow:
            return None
        
        # Find a free port
        try:
            port = find_free_port()
            st.info(f"🔌 Using port {port} for OAuth callback")
        except RuntimeError as e:
            st.error(f"❌ {e}")
            st.info("Please close other applications using ports 8080-8090 and try again")
            return None
        
        # Generate authorization URL with account selection prompt
        auth_url, _ = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='select_account consent',  # This forces account selection
            redirect_uri=f'http://localhost:{port}/'
        )
        
        return flow, auth_url, port
        
    except Exception as e:
        st.error(f"Authentication setup failed: {e}")
        return None

def authenticate_google_auto():
    """Automatic OAuth flow with account selection"""
    try:
        # Check if we have valid credentials
        creds = load_credentials()
        
        if creds and creds.valid:
            return creds
        
        # If we have credentials but they're expired, refresh them
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                save_credentials(creds)
                return creds
            except Exception as e:
                st.warning(f"Failed to refresh token: {e}")
                # Continue to re-authentication
        
        # Start new authentication flow
        flow = setup_oauth_flow()
        if not flow:
            return None
        
        # Find a free port
        try:
            port = find_free_port()
        except RuntimeError as e:
            st.error(f"❌ {e}")
            return None
        
        # Run the OAuth flow with account selection
        creds = flow.run_local_server(
            port=port,
            prompt='select_account consent',
            authorization_prompt_message="Please visit this URL to authorize the application: {url}",
            success_message="✅ Authentication successful! You can close this window and return to the app.",
            open_browser=True
        )
        
        # Save credentials
        save_credentials(creds)
        return creds
        
    except Exception as e:
        st.error(f"Authentication failed: {e}")
        return None

def setup_oauth_auth():
    """Setup OAuth authentication with account selection"""
    st.subheader("🔐 Google OAuth Authentication")
    
    # Check if credentials file exists
    if not os.path.exists(CREDENTIALS_FILE):
        st.error(f"❌ {CREDENTIALS_FILE} not found!")
        st.info("""
        **Setup Instructions:**
        1. Download your OAuth credentials from Google Cloud Console
        2. Rename the file to `credentials.json`
        3. Place it in the same directory as this app
        4. Restart the application
        """)
        return False
    
    st.success(f"✅ Found {CREDENTIALS_FILE}")
    
    # Show detected Gmail accounts
    detected_accounts = get_chrome_logged_in_accounts()
    if detected_accounts:
        st.info("🔍 **Detected Gmail accounts in your browser:**")
        for i, account in enumerate(detected_accounts, 1):
            st.write(f"{i}. **{account['email']}** ({account['name']}) - Profile: {account['profile']}")
        st.markdown("---")
    
    # Show current authentication status
    creds = load_credentials()
    if creds and creds.valid:
        st.info("✅ You already have valid credentials!")
        if st.button("🔄 Re-authenticate with Different Account"):
            if os.path.exists(TOKEN_FILE):
                os.remove(TOKEN_FILE)
            st.rerun()
        return True
    
    st.markdown("""
    ### 🎯 Account Selection Authentication:
    
    **The authentication will:**
    1. 🌐 Open Google's sign-in page in your browser
    2. 📧 **Show ALL your Gmail accounts** for selection
    3. ✅ Let you choose the correct account for YouTube Music
    4. 🔐 Complete secure OAuth authentication
    5. 🎵 Connect to your YouTube Music library
    """)
    
    # Authentication method selection
    auth_method = st.radio(
        "Choose authentication method:",
        [
            "🚀 Automatic (Recommended)",
            "📝 Manual (Copy-Paste Code)"
        ],
        help="Automatic opens browser and completes auth automatically. Manual requires copying a code."
    )
    
    if auth_method == "🚀 Automatic (Recommended)":
        if st.button("🔐 Start Authentication", type="primary"):
            with st.spinner("🔐 Starting authentication process..."):
                try:
                    creds = authenticate_google_auto()
                    
                    if creds:
                        # Initialize YTMusic
                        ytmusic = YTMusic()
                        
                        st.session_state.credentials = creds
                        st.session_state.ytmusic = ytmusic
                        st.session_state.auth_status = True
                        st.session_state.current_account = "OAuth User"
                        
                        st.success("🎉 Authentication successful!")
                        st.balloons()
                        st.rerun()
                        return True
                    else:
                        st.error("❌ Authentication failed. Please try the manual method.")
                        return False
                        
                except Exception as e:
                    st.error(f"❌ Authentication error: {e}")
                    st.info("💡 Try the manual authentication method below if automatic fails.")
                    return False
    
    else:  # Manual method
        if st.button("🔗 Generate Authentication URL", type="primary"):
            try:
                result = authenticate_google_manual()
                if result:
                    flow, auth_url, port = result
                    
                    st.session_state.oauth_flow = flow
                    st.session_state.auth_url = auth_url
                    st.session_state.oauth_port = port
                    
                    st.success("✅ Authentication URL generated!")
                    st.markdown(f"""
                    ### 🔗 Click the link below to authenticate:
                    **[🚀 Authenticate with Google (Account Selection)]({auth_url})**
                    
                    **Or copy this URL to your browser:**
                    """)
                    st.code(auth_url, language=None)
                    
                    # Try to open automatically
                    try:
                        webbrowser.open(auth_url)
                        st.info("🌐 Attempting to open in your default browser...")
                    except:
                        st.warning("⚠️ Could not open browser automatically. Please use the link above.")
                        
            except Exception as e:
                st.error(f"❌ Failed to generate authentication URL: {e}")
        
        # Manual code input
        if st.session_state.get('auth_url'):
            st.markdown("---")
            st.subheader("📝 Complete Authentication")
            
            auth_code = st.text_input(
                "Enter the authorization code from Google:",
                placeholder="Paste the code here...",
                help="After clicking the link above and selecting your account, Google will show you a code. Copy and paste it here."
            )
            
            if st.button("✅ Complete Authentication"):
                if auth_code.strip():
                    try:
                        with st.spinner("Completing authentication..."):
                            flow = st.session_state.oauth_flow
                            flow.fetch_token(code=auth_code.strip())
                            
                            creds = flow.credentials
                            save_credentials(creds)
                            
                            # Initialize YTMusic
                            ytmusic = YTMusic()
                            
                            st.session_state.credentials = creds
                            st.session_state.ytmusic = ytmusic
                            st.session_state.auth_status = True
                            st.session_state.current_account = "OAuth User"
                            
                            # Clear OAuth session data
                            st.session_state.oauth_flow = None
                            st.session_state.auth_url = None
                            
                            st.success("🎉 Authentication successful!")
                            st.balloons()
                            st.rerun()
                            return True
                            
                    except Exception as e:
                        st.error(f"❌ Authentication failed: {e}")
                        st.error("Please check your authorization code and try again.")
                        return False
                else:
                    st.warning("Please enter the authorization code")
    
    return False

def setup_headers_auth():
    """Setup browser headers authentication (fallback method)"""
    st.subheader("🌐 Browser Headers Authentication")
    
    st.warning("⚠️ This is a fallback method. OAuth is recommended for better security.")
    
    detected_accounts = get_chrome_logged_in_accounts()
    if detected_accounts:
        st.info("🔍 **Detected Gmail accounts in your browser:**")
        for account in detected_accounts:
            st.write(f"• **{account['email']}** ({account['name']})")
        st.warning("⚠️ Make sure you're using one of these accounts in YouTube Music")
    
    st.markdown("""
    ### Instructions:
    1. Open YouTube Music (music.youtube.com) in your browser
    2. **Make sure you're logged into the correct Google account**
    3. Open Developer Tools (F12)
    4. Go to Network tab
    5. Refresh the page or click on a song
    6. Find a request to 'music.youtube.com/youtubei/v1/'
    7. Right-click → Copy → Copy as cURL
    8. Paste the cURL command below
    """)
    
    headers_input = st.text_area(
        "Paste cURL command here:",
        height=150,
        placeholder="curl 'https://music.youtube.com/youtubei/v1/...' -H 'authorization: ...' ..."
    )
    
    account_name = st.text_input(
        "Account name:",
        value="headers_auth",
        help="Give this authentication a name"
    )
    
    if st.button("🔧 Setup Headers Authentication", type="primary"):
        if headers_input.strip():
            with st.spinner("Setting up headers authentication..."):
                try:
                    ytmusic = YTMusic(auth=headers_input)
                    ytmusic.get_home()  # Test connection
                    
                    st.session_state.ytmusic = ytmusic
                    st.session_state.auth_status = True
                    st.session_state.current_account = account_name
                    
                    st.success(f"✅ Headers authentication successful for: {account_name}")
                    return True
                except Exception as e:
                    st.error(f"❌ Headers authentication failed: {e}")
                    return False
        else:
            st.warning("Please paste the cURL command")
    
    return False

def authentication_tab():
    """Authentication tab content"""
    st.title("🔐 YouTube Music Authentication")
    
    if st.session_state.auth_status:
        st.success(f"✅ Authenticated as: {st.session_state.current_account}")
        
        # Show authentication details
        if st.session_state.credentials:
            with st.expander("🔍 Authentication Details"):
                st.write("**Authentication Type:** OAuth 2.0")
                if hasattr(st.session_state.credentials, 'expiry'):
                    st.write(f"**Token Expires:** {st.session_state.credentials.expiry}")
                st.write("**Scopes:** YouTube Music Access")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 Re-authenticate"):
                st.session_state.ytmusic = None
                st.session_state.auth_status = False
                st.session_state.current_account = None
                st.session_state.credentials = None
                st.session_state.oauth_flow = None
                st.session_state.auth_url = None
                if os.path.exists(TOKEN_FILE):
                    os.remove(TOKEN_FILE)
                st.rerun()
        
        with col2:
            if st.button("🧪 Test Connection"):
                try:
                    if st.session_state.ytmusic:
                        with st.spinner("Testing connection..."):
                            home_data = st.session_state.ytmusic.get_home()
                            st.success("✅ Connection test successful!")
                            st.info(f"Found {len(home_data)} home sections")
                    else:
                        st.error("❌ No YTMusic instance found")
                except Exception as e:
                    st.error(f"❌ Connection test failed: {e}")
        
        return
    
    st.info("🔐 Please authenticate with your Google account to access YouTube Music")
    
    # Check if credentials file exists
    if os.path.exists(CREDENTIALS_FILE):
        st.success(f"✅ Found {CREDENTIALS_FILE}")
    else:
        st.error(f"❌ {CREDENTIALS_FILE} not found!")
        st.info("""
        **Required Setup:**
        1. Download OAuth credentials from Google Cloud Console
        2. Rename to `credentials.json`
        3. Place in app directory
        """)
    
    auth_method = st.radio(
        "Choose authentication method:",
        ["🔐 OAuth (Recommended)", "🌐 Browser Headers (Fallback)"],
        help="OAuth provides secure authentication with account selection"
    )
    
    if auth_method == "🔐 OAuth (Recommended)":
        setup_oauth_auth()
    else:
        setup_headers_auth()

def detect_csv_columns(df):
    """Detect valid song columns in CSV"""
    valid_columns = ['Song', 'Songs', 'Names', 'Title', 'Track', 'Track Name', 'Name']
    for col in df.columns:
        if any(valid.lower() in col.lower() for valid in valid_columns):
            return col
    return None

async def process_playlist(yt_handler, csv_df, playlist_name, song_column, privacy_setting):
    """Process playlist creation with OAuth authenticated user"""
    results = {
        'total_songs': 0,
        'matched_songs': 0,
        'unmatched_songs': [],
        'matched_details': [],
        'errors': []
    }
    
    try:
        # Map privacy setting
        privacy_map = {
            "Private": "PRIVATE",
            "Unlisted": "UNLISTED", 
            "Public": "PUBLIC"
        }
        privacy = privacy_map.get(privacy_setting, "PRIVATE")
        
        # Create playlist
        playlist_id = await yt_handler.create_playlist(
            playlist_name,
            f"Created from CSV on {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            privacy
        )
        
        st.success(f"🎵 Playlist '{playlist_name}' created successfully!")
        
        total_songs = len(csv_df)
        progress_bar = st.progress(0)
        status_text = st.empty()
        results_container = st.empty()
        
        # Process each song
        for index, row in csv_df.iterrows():
            results['total_songs'] += 1
            progress = (index + 1) / total_songs
            
            song_name = str(row[song_column]).strip()
            if not song_name or song_name.lower() in ['nan', 'none', '']:
                continue
                
            status_text.text(f"🔍 Searching ({index + 1}/{total_songs}): {song_name}")
            
            try:
                # Search for song
                matches = await yt_handler.search_song(song_name)
                
                if matches and len(matches) > 0:
                    best_match = matches[0]
                    video_id = best_match['videoId']
                    
                    # Add to playlist
                    await yt_handler.add_to_playlist(playlist_id, video_id)
                    
                    results['matched_songs'] += 1
                    artist_name = "Unknown Artist"
                    if 'artists' in best_match and len(best_match['artists']) > 0:
                        artist_name = best_match['artists'][0].get('name', 'Unknown Artist')
                    
                    results['matched_details'].append({
                        'original': song_name,
                        'matched': best_match.get('title', 'Unknown'),
                        'artist': artist_name,
                        'status': '✅ Added',
                        'url': f"https://music.youtube.com/watch?v={video_id}"
                    })
                    
                    status_text.text(f"✅ Added: {song_name}")
                    
                else:
                    results['unmatched_songs'].append(song_name)
                    results['matched_details'].append({
                        'original': song_name,
                        'matched': '',
                        'artist': '',
                        'status': '❌ Not Found',
                        'url': ''
                    })
                    status_text.text(f"❌ Not found: {song_name}")
                    
            except Exception as e:
                error_msg = str(e)
                results['errors'].append({
                    'song': song_name,
                    'error': error_msg
                })
                results['matched_details'].append({
                    'original': song_name,
                    'matched': '',
                    'artist': '',
                    'status': f'⚠️ Error: {error_msg[:50]}...',
                    'url': ''
                })
                status_text.text(f"⚠️ Error processing: {song_name}")
            
            progress_bar.progress(progress)
            
            # Show live results
            if (index + 1) % 5 == 0 or index == total_songs - 1:
                with results_container.container():
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("✅ Added", results['matched_songs'])
                    with col2:
                        st.metric("❌ Not Found", len(results['unmatched_songs']))
                    with col3:
                        st.metric("⚠️ Errors", len(results['errors']))
        
        status_text.text("🎉 Playlist creation complete!")
        return results, playlist_id
        
    except Exception as e:
        st.error(f"❌ Playlist creation failed: {e}")
        raise

def playlist_creation_tab():
    """Playlist creation tab content"""
    st.title("🎵 Create YouTube Music Playlist from CSV")
    
    if not st.session_state.auth_status:
        st.warning("⚠️ Please authenticate first in the Authentication tab")
        st.info("👆 Go to the 'Authentication' tab and sign in with your Google account")
        return
    
    st.success(f"✅ Authenticated as: {st.session_state.current_account}")
    
    # File upload section
    st.subheader("📁 Upload Your Music List")
    uploaded_file = st.file_uploader(
        "Upload CSV file with song names",
        type=['csv'],
        help="Your CSV file should contain a column with song names (e.g., 'Song', 'Title', 'Track')"
    )
    
    if uploaded_file is not None:
        try:
            # Read CSV with different encodings
            try:
                df = pd.read_csv(uploaded_file, encoding='utf-8')
            except UnicodeDecodeError:
                df = pd.read_csv(uploaded_file, encoding='latin-1')
            
            # Detect song column
            song_column = detect_csv_columns(df)
            
            if not song_column:
                st.error("❌ Could not find a valid song column.")
                st.info("Please ensure your CSV has a column named: 'Song', 'Songs', 'Names', 'Title', 'Track', or 'Track Name'")
                
                # Show available columns
                st.write("**Available columns in your CSV:**")
                for col in df.columns:
                    st.write(f"• {col}")
                
                # Allow manual column selection
                selected_column = st.selectbox(
                    "Or select a column manually:",
                    [""] + list(df.columns),
                    help="Choose which column contains the song names"
                )
                
                if selected_column:
                    song_column = selected_column
                else:
                    return
            
            st.success(f"✅ Detected song column: **'{song_column}'**")
            
            # Remove empty rows
            df = df.dropna(subset=[song_column])
            df = df[df[song_column].astype(str).str.strip() != '']
            
            # Show CSV info
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Total Songs Found", len(df))
            with col2:
                st.metric("Columns in CSV", len(df.columns))
            
            # Show preview
            with st.expander("📋 Preview first 10 songs", expanded=True):
                preview_df = df[[song_column]].head(10).copy()
                preview_df.index = preview_df.index + 1
                st.dataframe(preview_df, use_container_width=True)
            
            # Playlist configuration
            st.subheader("⚙️ Playlist Settings")
            
            col1, col2 = st.columns(2)
            
            with col1:
                playlist_name = st.text_input(
                    "🎵 Playlist name:",
                    value=f"My Playlist {datetime.now().strftime('%Y-%m-%d')}",
                    help="Enter a name for your new YouTube Music playlist"
                )
            
            with col2:
                playlist_privacy = st.selectbox(
                    "🔒 Playlist privacy:",
                    ["Private", "Unlisted", "Public"],
                    index=0,
                    help="Private: Only you can see it | Unlisted: Anyone with link | Public: Everyone can find it"
                )
            
            # Additional options
            with st.expander("🔧 Advanced Options"):
                col1, col2 = st.columns(2)
                with col1:
                    skip_duplicates = st.checkbox(
                        "Skip duplicate songs",
                        value=True,
                        help="Avoid adding the same song multiple times"
                    )
                with col2:
                    max_songs = st.number_input(
                        "Maximum songs to process",
                        min_value=1,
                        max_value=len(df),
                        value=min(len(df), 100),
                        help="Limit the number of songs to process"
                    )
            
            # Create playlist button
            st.markdown("---")
            
            if st.button("🚀 Create Playlist", type="primary", help="This will create the playlist in your YouTube Music account"):
                if playlist_name.strip():
                    # Prepare dataframe
                    process_df = df.head(max_songs) if max_songs < len(df) else df
                    
                    if skip_duplicates:
                        process_df = process_df.drop_duplicates(subset=[song_column])
                    
                    st.info(f"🎵 Creating playlist with {len(process_df)} songs...")
                    
                    # Initialize YouTube handler
                    yt_handler = YouTubeMusicHandler()
                    yt_handler.set_ytmusic(st.session_state.ytmusic)
                    
                    # Process playlist
                    try:
                        with st.spinner("🎵 Creating playlist and adding songs..."):
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            results, playlist_id = loop.run_until_complete(
                                process_playlist(yt_handler, process_df, playlist_name, song_column, playlist_privacy)
                            )
                            
                            st.session_state.transfer_results = results
                        
                        # Show final results
                        st.markdown("---")
                        st.subheader("🎉 Playlist Created Successfully!")
                        
                        # Results metrics
                        col1, col2, col3, col4 = st.columns(4)
                        
                        with col1:
                            st.metric("🎵 Total Songs", results['total_songs'])
                        
                        with col2:
                            st.metric("✅ Successfully Added", results['matched_songs'])
                            
                        with col3:
                            st.metric("❌ Not Found", len(results['unmatched_songs']))
                        
                        with col4:
                            st.metric("⚠️ Errors", len(results['errors']))
                        
                        # Success rate
                        if results['total_songs'] > 0:
                            success_rate = (results['matched_songs'] / results['total_songs']) * 100
                            st.progress(success_rate / 100)
                            st.write(f"**Success Rate: {success_rate:.1f}%**")
                        
                        # Create results dataframe
                        results_df = pd.DataFrame(results['matched_details'])
                        
                        # Show detailed results
                        with st.expander("📊 Detailed Results", expanded=True):
                            st.dataframe(results_df, use_container_width=True)
                        
                        # Playlist link
                        if playlist_id:
                            playlist_url = f"https://music.youtube.com/playlist?list={playlist_id}"
                            st.success(f"🎵 **[Open Playlist in YouTube Music]({playlist_url})**")
                        
                        # Export options
                        st.subheader("📥 Export Results")
                        
                        col1, col2 = st.columns(2)
                        
                        with col1:
                            csv_export = results_df.to_csv(index=False)
                            st.download_button(
                                label="📄 Download Full Results CSV",
                                data=csv_export,
                                file_name=f"playlist_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                                mime="text/csv"
                            )
                        
                        with col2:
                            if results['unmatched_songs']:
                                unmatched_df = pd.DataFrame({
                                    'Unmatched Songs': results['unmatched_songs']
                                })
                                csv_unmatched = unmatched_df.to_csv(index=False)
                                st.download_button(
                                    label="❌ Download Unmatched Songs CSV",
                                    data=csv_unmatched,
                                    file_name=f"unmatched_songs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                                    mime="text/csv"
                                )
                        
                        # Show unmatched songs if any
                        if results['unmatched_songs']:
                            with st.expander(f"❌ Unmatched Songs ({len(results['unmatched_songs'])})"):
                                for i, song in enumerate(results['unmatched_songs'], 1):
                                    st.write(f"{i}. {song}")
                        
                        # Show errors if any
                        if results['errors']:
                            with st.expander(f"⚠️ Errors ({len(results['errors'])})"):
                                for i, error in enumerate(results['errors'], 1):
                                    st.write(f"{i}. **{error['song']}**: {error['error']}")
                        
                    except Exception as e:
                        st.error(f"❌ Failed to create playlist: {e}")
                        st.error("Please check your authentication and try again.")
                        logger.error(f"Playlist creation error: {traceback.format_exc()}")
                        
                else:
                    st.warning("⚠️ Please enter a playlist name")
                    
        except Exception as e:
            st.error(f"❌ Error reading CSV file: {e}")
            st.info("Please check that your file is a valid CSV format")

def sidebar_content():
    """Sidebar content with libraries and usage stats"""
    st.sidebar.title("📊 App Information")
    
    # File status
    with st.sidebar.expander("📁 File Status", expanded=True):
        if os.path.exists(CREDENTIALS_FILE):
            st.success(f"✅ {CREDENTIALS_FILE} found")
        else:
            st.error(f"❌ {CREDENTIALS_FILE} missing")
        
        if os.path.exists(TOKEN_FILE):
            st.success(f"✅ {TOKEN_FILE} found")
        else:
            st.info(f"ℹ️ {TOKEN_FILE} not found (will be created after auth)")
    
    # Port status
    with st.sidebar.expander("🔌 Port Status"):
        try:
            free_port = find_free_port()
            st.success(f"✅ Port {free_port} available")
        except RuntimeError:
            st.error("❌ No free ports (8080-8090)")
    
    # Detected accounts
    detected_accounts = get_chrome_logged_in_accounts()
    with st.sidebar.expander("📧 Detected Gmail Accounts"):
        if detected_accounts:
            for account in detected_accounts:
                st.write(f"📧 {account['email']}")
        else:
            st.info("No Gmail accounts detected")
    
    # Authentication status
    with st.sidebar.expander("🔐 Authentication Status", expanded=True):
        if st.session_state.auth_status:
            st.success(f"✅ Authenticated")
            st.write(f"**Account:** {st.session_state.current_account}")
            if st.session_state.credentials:
                st.write("**Method:** OAuth 2.0")
        else:
            st.error("❌ Not Authenticated")
            st.write("Please sign in to continue")
    
    # Libraries section
    with st.sidebar.expander("📚 Libraries & Versions"):
        libraries = {
            "streamlit": st.__version__,
            "pandas": pd.__version__,
            "ytmusicapi": "1.3.2",
            "google-auth": "2.17.3",
            "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        }
        
        for lib, version in libraries.items():
            st.code(f"{lib}: {version}")
    
    # API Usage section
    with st.sidebar.expander("🎯 API Usage"):
        usage = st.session_state.token_usage
        
        st.metric("Total API Calls", usage['api_calls'])
        st.metric("Search Queries", usage['search_queries'])
        st.metric("Est. Tokens Used", usage['total_tokens'])
        
        # Estimated cost (mock calculation)
        est_cost = (usage['total_tokens'] / 1000) * 0.001
        st.metric("Estimated Cost", f"${est_cost:.4f}")

def main():
    """Main app function"""
    # Sidebar
    sidebar_content()
    
    # Main header
    st.markdown("""
    # 🎵 YouTube Music Playlist Creator
    **Transform your song lists into YouTube Music playlists automatically**
    """)
    
    # Main content with tabs
    tab1, tab2 = st.tabs(["🔐 Authentication", "🎵 Create Playlist"])
    
    with tab1:
        authentication_tab()
    
    with tab2:
        playlist_creation_tab()
    
    # Footer
    st.markdown("---")
    st.markdown(
        "<p style='text-align: center; color: gray; font-size: 0.8em;'>"
        "YouTube Music Playlist Creator | Made with Streamlit | "
        "Secure OAuth Authentication with Account Selection"
        "</p>",
        unsafe_allow_html=True
    )

if __name__ == "__main__":
    main()