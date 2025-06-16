import streamlit as st
from ytmusicapi import YTMusic
import logging
import json
import os
import pickle
import subprocess
import sqlite3
import platform
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
import webbrowser
import time
import shutil
import pandas as pd
from typing import Dict, List, Optional
import unicodedata
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential
from aiohttp import ClientError # Assuming aiohttp is already installed or pulled by ytmusicapi
from datetime import datetime
import csv
import io # For in-memory CSV operations

# ==============================================================================
# Global Configuration & Logging
# ==============================================================================

# Suppress debug logs from httpx and urllib3 if not needed, useful for cleaner output
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("google.auth.transport.requests").setLevel(logging.WARNING)


# Configure logging for the application
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables (e.g., for RATE_LIMIT_CALLS_PER_SECOND)
load_dotenv()

# OAuth 2.0 scopes for YouTube Music (required for ytmusicapi OAuth flow)
SCOPES = ['https://www.googleapis.com/auth/youtube']

# Streamlit Page Configuration
st.set_page_config(
    page_title="🎶 YT Music Playlist Transfer",
    page_icon="🎵",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==============================================================================
# Utility Functions (from youtube_auth.py)
# These functions are adapted to be part of the Streamlit app's context
# They directly use st.session_state for storing the authenticated YTMusic object
# ==============================================================================

def get_chrome_logged_in_accounts():
    """Get logged-in Google accounts from Chrome profiles."""
    try:
        accounts = []
        system = platform.system()
        
        if system == "Windows":
            chrome_user_data = os.path.expanduser(r"~\AppData\Local\Google\Chrome\User Data")
        elif system == "Darwin":  # macOS
            chrome_user_data = os.path.expanduser("~/Library/Application Support/Google/Chrome")
        else:  # Linux
            chrome_user_data = os.path.expanduser("~/.config/google-chrome")
            
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
                            
                        if 'account_info' in prefs:
                            for account in prefs['account_info']:
                                if 'email' in account:
                                    accounts.append({
                                        'email': account['email'],
                                        'name': account.get('full_name', account['email'].split('@')[0]),
                                        'profile': item
                                    })
                        elif 'signin' in prefs and 'allowed_username' in prefs['signin']:
                            email = prefs['signin']['allowed_username']
                            if email and '@' in email: # Basic email validation
                                accounts.append({
                                    'email': email,
                                    'name': email.split('@')[0],
                                    'profile': item
                                })
                            
                    except (json.JSONDecodeError, KeyError, FileNotFoundError):
                        pass # Ignore errors in reading preferences
                        
                login_db = os.path.join(profile_path, "Login Data")
                if os.path.exists(login_db):
                    try:
                        temp_db = login_db + "_temp"
                        shutil.copy2(login_db, temp_db) # Copy to avoid locking
                        
                        conn = sqlite3.connect(temp_db)
                        cursor = conn.cursor()
                        
                        cursor.execute("""
                            SELECT origin_url, username_value 
                            FROM logins 
                            WHERE origin_url LIKE '%google.com%' 
                               OR origin_url LIKE '%youtube.com%'
                               OR origin_url LIKE '%gmail.com%'
                        """)
                        
                        for row in cursor.fetchall():
                            if row[1] and '@' in row[1]: # Valid email
                                email = row[1]
                                if not any(acc['email'] == email for acc in accounts): # Avoid duplicates
                                    accounts.append({
                                        'email': email,
                                        'name': email.split('@')[0],
                                        'profile': item
                                    })
                        
                        conn.close()
                        os.remove(temp_db) # Clean up temp file
                        
                    except Exception:
                        pass # Ignore errors in reading login data
                        
        return accounts
        
    except Exception as e:
        logger.debug(f"Error getting Chrome accounts: {e}")
        return []

def get_system_google_accounts():
    """Get Google accounts from system-wide credential stores."""
    try:
        accounts = []
        system = platform.system()
        
        if system == "Windows":
            try:
                import keyring
                # This is a simplified approach, keyring might store many credentials
                stored_creds = keyring.get_credential("google.com", None)
                if stored_creds and stored_creds.username and '@' in stored_creds.username:
                    accounts.append({
                        'email': stored_creds.username,
                        'name': stored_creds.username.split('@')[0],
                        'source': 'Windows Credential Manager'
                    })
            except ImportError:
                pass # keyring not installed
            except Exception:
                pass # other keyring errors
                
        elif system == "Darwin":  # macOS
            try:
                # Use security command to query keychain. Requires user permission possibly.
                result = subprocess.run([
                    'security', 'find-internet-password', 
                    '-s', 'accounts.google.com', # Service for Google accounts
                    '-g' # show password, which for internet-password shows all details including account name
                ], capture_output=True, text=True, check=False) # check=False to handle non-zero exit for not found
                
                if result.returncode == 0:
                    for line in result.stderr.split('\n'): # Output is often to stderr for some reason
                        if 'acct' in line and '@' in line:
                            email = line.split('"')[1] # Extract email from "acct"<blob> = "<email>"
                            accounts.append({
                                'email': email,
                                'name': email.split('@')[0],
                                'source': 'macOS Keychain'
                            })
                            break # Assume one main account for now
            except Exception:
                pass # security command not found or other errors
                
        return accounts
        
    except Exception as e:
        logger.debug(f"Error getting system accounts: {e}")
        return []

def get_detected_google_accounts():
    """Combines accounts from Chrome and system credential stores, removes duplicates."""
    accounts = []
    chrome_accounts = get_chrome_logged_in_accounts()
    accounts.extend(chrome_accounts)
    system_accounts = get_system_google_accounts()
    accounts.extend(system_accounts)
    
    unique_accounts = {}
    for account in accounts:
        email = account['email']
        if email not in unique_accounts:
            unique_accounts[email] = account
            
    return list(unique_accounts.values())

def list_available_google_accounts():
    """Lists accounts for which YTMusic API credentials have been saved."""
    try:
        accounts = []
        credentials_dir = os.path.expanduser('~/.config/ytmusicapi/') # Default path for ytmusicapi credentials
        
        if not os.path.exists(credentials_dir):
            os.makedirs(credentials_dir, exist_ok=True) # Create if not exists, avoid error if already exists
            return accounts
            
        for filename in os.listdir(credentials_dir):
            if filename.startswith('credentials_') and filename.endswith('.json'):
                account_name = filename.replace('credentials_', '').replace('.json', '')
                accounts.append(account_name)
                
        return accounts
        
    except Exception as e:
        logger.error(f"Error listing saved YTMusic accounts: {e}")
        return []

def get_user_info_from_credentials(creds_path):
    """(Attempt to) extract user info from stored OAuth credentials."""
    try:
        with open(creds_path, 'r') as f:
            cred_data = json.load(f)
            
        # These files typically come from google-auth-oauthlib.
        # They don't usually contain the user's email directly, but rather client IDs, refresh tokens etc.
        # The 'client_id' might be generic for the app, not user-specific.
        # For simplicity, we just return "Stored Account" as a placeholder for identified files.
        return "Stored Account" 
        
    except Exception as e:
        logger.debug(f"Error reading credentials info from {creds_path}: {e}")
        return "Unknown Account"

# ==============================================================================
# Streamlit UI Pages (from youtube_auth.py - adapted for multi-tab app)
# ==============================================================================

def setup_oauth_page():
    """Streamlit UI for OAuth authentication setup."""
    st.header("🔐 OAuth Authentication Setup")
    
    credentials_dir = os.path.expanduser('~/.config/ytmusicapi/')
    accounts = list_available_google_accounts()
    
    if accounts:
        st.subheader("Existing Accounts")
        account_options = []
        for account in accounts:
            creds_path = os.path.join(credentials_dir, f'credentials_{account}.json')
            user_info = get_user_info_from_credentials(creds_path)
            account_options.append(f"{account} ({user_info})")
        
        account_options.append("➕ Add new Google account")
        
        selected_option = st.selectbox(
            "Select an account or add new:",
            account_options,
            key="oauth_account_selection"
        )
        
        if st.button("Use Selected Account", key="use_oauth_account"):
            if selected_option == "➕ Add new Google account":
                st.session_state.show_new_oauth = True # Flag to show new account setup
            else:
                selected_account = accounts[account_options.index(selected_option.split(' (')[0])] # Get actual name from display option
                creds_path = os.path.join(credentials_dir, f'credentials_{selected_account}.json')
                
                with st.spinner(f"Authenticating with '{selected_account}'..."):
                    try:
                        # Load and refresh credentials if needed
                        creds = Credentials.from_authorized_user_file(creds_path, SCOPES)
                        
                        if not creds or not creds.valid:
                            if creds and creds.expired and creds.refresh_token:
                                logger.info(f"Refreshing expired credentials for {selected_account}...")
                                creds.refresh(Request())
                                # Save refreshed credentials back
                                with open(creds_path, 'w') as token:
                                    token.write(creds.to_json())
                            else:
                                st.error("Credentials invalid or expired. Please re-authenticate by adding a new account.")
                                st.session_state.show_new_oauth = True
                                return
                        
                        # Initialize YTMusic with the loaded credentials file path
                        ytmusic = YTMusic(auth=creds_path)
                        # Perform a small API call to verify the authentication
                        ytmusic.get_home() 
                        
                        st.success(f"✅ Successfully authenticated with account: **{selected_account}**")
                        st.session_state.ytmusic_object = ytmusic # Store the YTMusic object in session state
                        st.session_state.current_account_name = selected_account # Store the account name
                        
                    except Exception as e:
                        st.error(f"Authentication failed: {e}. Please try again or create a new account.")
                        logger.error(f"OAuth authentication failed for {selected_account}: {e}", exc_info=True)
    else:
        st.info("No existing accounts found. Please add a new account to proceed.")
        st.session_state.show_new_oauth = True # New account setup is automatically triggered if no existing

    # Display new OAuth setup section if flagged
    if st.session_state.get('show_new_oauth', False):
        setup_new_oauth_account()

def setup_new_oauth_account():
    """Streamlit UI for setting up a new OAuth account."""
    st.subheader("🆕 Add New Google Account")
    
    detected_accounts = get_detected_google_accounts()
    
    account_name_final = "default" # Default if no specific name is chosen
    
    if detected_accounts:
        st.info("🔍 Detected Google Accounts in your system (suggested for naming):")
        account_display_options = ["Enter custom name"]
        for account in detected_accounts:
            source = account.get('source', f"Chrome Profile: {account.get('profile', 'Default')}")
            display_text = f"{account['email']} ({account['name']}) - {source}"
            account_display_options.append(display_text)
            st.write(f"• {display_text}")
        
        st.write("---")
        
        col1, col2 = st.columns(2)
        
        with col1:
            use_detected_option = st.selectbox(
                "Select detected account for naming (or choose custom):",
                account_display_options,
                key="detected_account_selection"
            )
        
        with col2:
            account_name_input_key = "custom_oauth_name"
            account_name_default_val = ""
            
            if use_detected_option != "Enter custom name":
                try:
                    # Extract email from the display string (e.g., "email@domain.com (Name) - Source")
                    matched_email = use_detected_option.split(" (")[0] 
                    selected_account_info = next((acc for acc in detected_accounts if acc['email'] == matched_email), None)
                    
                    if selected_account_info:
                        account_name_input_key = "suggested_oauth_name"
                        account_name_default_val = selected_account_info['name']
                        st.text_input(
                            f"Account Name (from {selected_account_info['email']}):",
                            value=account_name_default_val,
                            key=account_name_input_key
                        )
                    else:
                        st.text_input("Account Name:", placeholder="e.g., 'personal', 'work'", key="manual_oauth_name_fallback")
                except Exception as e:
                    logger.error(f"Error processing selected detected account: {e}", exc_info=True)
                    st.text_input("Account Name:", placeholder="e.g., 'personal', 'work'", key="manual_oauth_name_error")
            else:
                st.text_input(
                    "Account Name:",
                    placeholder="e.g., 'personal', 'work'",
                    key=account_name_input_key
                )
        
        # Get the final account name from Streamlit's state after all inputs are rendered
        if use_detected_option == "Enter custom name":
            account_name_final = st.session_state.get("custom_oauth_name", "").strip()
        else:
            account_name_final = st.session_state.get(account_name_input_key, "").strip() # Use the key that was actually rendered

    else: # No accounts detected
        st.warning("No Google accounts detected in your browser or system. Please enter a custom name.")
        account_name_final = st.text_input(
            "Account Name:",
            placeholder="e.g., 'personal', 'work'",
            key="manual_oauth_name"
        ).strip()
    
    if st.button("🚀 Start OAuth Authentication", key="start_new_oauth_button"):
        if not account_name_final:
            account_name_final = "default"
        
        credentials_dir = os.path.expanduser('~/.config/ytmusicapi/')
        os.makedirs(credentials_dir, exist_ok=True) # Ensure directory exists
        
        creds_path = os.path.join(credentials_dir, f'credentials_{account_name_final}.json')
        
        with st.spinner("Setting up OAuth... A browser window will open. Please complete the authentication flow. Check your terminal for browser output."):
            try:
                # YTMusic() with no arguments initiates OAuth flow and saves to ~/.config/ytmusicapi/oauth.json by default
                ytmusic = YTMusic() 
                ytmusic.get_home() # Test connection to ensure auth successful
                
                # After successful auth, ytmusicapi creates oauth.json (or similar)
                # We need to find that file and rename it to our desired credentials_account_name.json
                default_oauth_path = os.path.join(credentials_dir, 'oauth.json')
                
                # Wait a bit for the file to be written, if it's async
                max_wait_time = 10 # seconds
                start_time = time.time()
                while not os.path.exists(default_oauth_path) and (time.time() - start_time) < max_wait_time:
                    time.sleep(0.5)

                if os.path.exists(default_oauth_path):
                    shutil.move(default_oauth_path, creds_path) # Rename and move to custom path/name
                    st.success(f"✅ New account '**{account_name_final}**' authenticated successfully!")
                    st.info(f"Credentials saved to: **{creds_path}**")
                    st.session_state.ytmusic_object = ytmusic
                    st.session_state.current_account_name = account_name_final
                    st.session_state.show_new_oauth = False # Hide new account setup section
                else: 
                    st.error("Failed to find the generated OAuth credentials file. Authentication might not have completed.")
                    logger.error("Default oauth.json not found after authentication attempt.")
                
            except Exception as e:
                st.error(f"OAuth setup failed: {e}. Please ensure you completed the browser authentication.")
                logger.error(f"New OAuth setup failed: {e}", exc_info=True)

def setup_headers_page():
    """Streamlit UI for browser headers authentication."""
    st.header("🌐 Browser Headers Authentication")
    
    detected_accounts = get_detected_google_accounts()
    
    if detected_accounts:
        st.info("🔍 Detected Google accounts in your system:")
        for account in detected_accounts:
            st.write(f"• {account['email']} (**{account['name']}** from {account.get('profile', 'Default Profile')})")
        st.warning("Make sure you're logged into the **correct account** in YouTube Music before copying cURL command.")
    
    st.subheader("Instructions to get cURL command:")
    st.markdown("""
    1.  Open YouTube Music (music.youtube.com) in your browser.
    2.  Ensure you are logged into the desired Google account.
    3.  Open **Developer Tools** (usually by pressing `F12` or `Ctrl+Shift+I` on Windows/Linux, `Cmd+Option+I` on macOS).
    4.  Go to the **`Network`** tab.
    5.  **Refresh the page** or click on any song/playlist to trigger network requests.
    6.  Look for a request named `browse`, `search`, `next`, or generally to `music.youtube.com/youtubei/v1/`.
    7.  **Right-click** on that request → `Copy` → `Copy as cURL (bash)`.
    8.  Paste the entire copied cURL command into the text area below.
    """)
    
    headers_raw = st.text_area(
        "Paste the copied cURL command here:",
        height=200,
        placeholder="curl 'https://music.youtube.com/youtubei/v1/...' -H 'authorization: ...' ...",
        key="headers_input"
    )
    
    col1, col2 = st.columns(2)
    
    with col1:
        account_options = ["Enter custom name"]
        if detected_accounts:
            account_options.extend([f"{acc['name']} ({acc['email']})" for acc in detected_accounts])
        
        selected_option = st.selectbox(
            "Associate headers with which account?",
            account_options,
            key="headers_account_selection"
        )
    
    with col2:
        account_name_input_key = "headers_custom_name"
        account_name_default_val = ""

        if selected_option == "Enter custom name":
            st.text_input(
                "Account Name:",
                placeholder="e.g., 'personal', 'work'",
                key=account_name_input_key
            )
        else:
            matched_email = selected_option.split(" (")[0]
            selected_account_info = next((acc for acc in detected_accounts if acc['email'] == matched_email), None)
            
            if selected_account_info:
                account_name_input_key = "headers_suggested_name"
                account_name_default_val = selected_account_info['name']
                st.text_input(
                    f"Account Name (from {selected_account_info['email']}):",
                    value=account_name_default_val,
                    key=account_name_input_key
                )
            else: # Fallback if selected but not found (e.g., data mismatch)
                 st.text_input("Account Name:", placeholder="e.g., 'personal', 'work'", key="headers_manual_fallback")

        account_name_final = st.session_state.get(account_name_input_key, "").strip() # Get the final value

    if st.button("🔗 Setup Headers Authentication", key="setup_headers_button"):
        if not headers_raw.strip():
            st.error("Please paste the cURL command to proceed.")
            return
        
        if not account_name_final:
            account_name_final = "default"
            
        with st.spinner("Setting up headers authentication..."):
            try:
                headers_dir = os.path.expanduser('~/.config/ytmusicapi/')
                os.makedirs(headers_dir, exist_ok=True)
                
                # You could parse the cURL string and save specific headers to a JSON file here
                # For simplicity, YTMusic(auth=headers_raw) uses the raw string directly but doesn't save it.
                # We'll save a dummy file to mark this account as having headers set up.
                headers_path = os.path.join(headers_dir, f'headers_{account_name_final}.json')
                with open(headers_path, 'w') as f:
                    f.write(json.dumps({"curl_command_snippet": headers_raw[:200] + "..."})) # Save a snippet
                
                # Initialize with headers (YTMusicapi will parse the raw cURL string)
                ytmusic = YTMusic(auth=headers_raw)
                ytmusic.get_home() # Test connection
                
                st.success(f"✅ Headers authentication successful for account: **{account_name_final}**")
                st.info(f"Headers associated with: **{headers_path}**")
                st.session_state.ytmusic_object = ytmusic
                st.session_state.current_account_name = account_name_final
                
            except Exception as e:
                st.error(f"Headers authentication failed: {e}. Please ensure the cURL command is correct and not expired.")
                logger.error(f"Headers authentication failed: {e}", exc_info=True)

def setup_cookies_page():
    """Streamlit UI for cookie-based authentication."""
    st.header("🍪 Cookie-based Authentication")
    
    try:
        import browser_cookie3
        cookies_available = True
    except ImportError:
        cookies_available = False
        st.error("`browser_cookie3` not installed. Install with: `pip install browser_cookie3`")
        return # Exit the function if dependency is missing
    
    detected_accounts = get_detected_google_accounts()
    
    if detected_accounts:
        st.info("🔍 Detected Google accounts in your system:")
        for account in detected_accounts:
            st.write(f"• {account['email']} (**{account['name']}** from {account.get('profile', 'Default Profile')})")
        st.warning("Make sure you're logged into the **correct YouTube Music account** in the selected browser.")
    
    st.subheader("Instructions:")
    st.markdown("""
    1.  Ensure you are logged into your desired YouTube Music account in your selected browser.
    2.  It's recommended to close other browser tabs or profiles associated with different Google accounts to avoid conflicts.
    3.  Select your browser below.
    """)
    
    col1, col2 = st.columns(2)
    
    with col1:
        browser_choice = st.selectbox(
            "Select your browser:",
            ["Chrome", "Firefox", "Edge", "Safari"],
            key="browser_selection"
        )
    
    with col2:
        account_options = ["Enter custom name"]
        if detected_accounts:
            account_options.extend([f"{acc['name']} ({acc['email']})" for acc in detected_accounts])
        
        selected_option = st.selectbox(
            "Associate cookies with which account?",
            account_options,
            key="cookies_account_selection"
        )
    
    account_name_input_key = "cookies_custom_name"
    account_name_default_val = ""

    if selected_option == "Enter custom name":
        st.text_input(
            "Account Name:",
            placeholder="e.g., 'personal', 'work'",
            key=account_name_input_key
        )
    else:
        matched_email = selected_option.split(" (")[0]
        selected_account_info = next((acc for acc in detected_accounts if acc['email'] == matched_email), None)
        
        if selected_account_info:
            account_name_input_key = "cookies_suggested_name"
            account_name_default_val = selected_account_info['name']
            st.text_input(
                f"Account Name (from {selected_account_info['email']}):",
                value=account_name_default_val,
                key=account_name_input_key
            )
        else: # Fallback
            st.text_input("Account Name:", placeholder="e.g., 'personal', 'work'", key="cookies_manual_fallback")

    account_name_final = st.session_state.get(account_name_input_key, "").strip()

    if st.button("🍪 Setup Cookie Authentication", key="setup_cookies_button"):
        if not account_name_final:
            account_name_final = "cookie_account"
        
        with st.spinner(f"Attempting to retrieve cookies from {browser_choice}..."):
            try:
                import browser_cookie3
                
                cookies = None
                if browser_choice == "Chrome":
                    cookies = browser_cookie3.chrome(domain_name='music.youtube.com')
                elif browser_choice == "Firefox":
                    cookies = browser_cookie3.firefox(domain_name='music.youtube.com')
                elif browser_choice == "Edge":
                    cookies = browser_cookie3.edge(domain_name='music.youtube.com')
                elif browser_choice == "Safari":
                    cookies = browser_cookie3.safari(domain_name='music.youtube.com')
                
                if not cookies:
                    st.error(f"Could not retrieve cookies from {browser_choice}. Ensure browser is open, you are logged in, and browser_cookie3 supports your browser version.")
                    return

                ytmusic = YTMusic(auth=cookies)
                ytmusic.get_home() # Test connection
                
                st.success(f"✅ Cookie authentication successful for account: **{account_name_final}**")
                st.session_state.ytmusic_object = ytmusic
                st.session_state.current_account_name = account_name_final
                
            except Exception as e:
                st.error(f"Cookie authentication failed: {e}. Please ensure browser is open and you are logged into YouTube Music.")
                logger.error(f"Cookie authentication failed for {browser_choice}: {e}", exc_info=True)

def list_accounts_page():
    """Streamlit UI to list configured accounts and detected accounts."""
    st.header("📋 Configured Accounts")
    
    credentials_dir = os.path.expanduser('~/.config/ytmusicapi/')
    
    if not os.path.exists(credentials_dir):
        st.info("No configured accounts found in ~/.config/ytmusicapi/.")
        return
    
    oauth_accounts = []
    header_accounts = []
    
    for filename in os.listdir(credentials_dir):
        if filename.startswith('credentials_') and filename.endswith('.json'):
            oauth_accounts.append(filename.replace('credentials_', '').replace('.json', ''))
        elif filename.startswith('headers_') and filename.endswith('.json'):
            header_accounts.append(filename.replace('headers_', '').replace('.json', ''))
    
    col1, col2 = st.columns(2)
    
    with col1:
        if oauth_accounts:
            st.subheader("🔐 Saved OAuth Accounts")
            for account in sorted(oauth_accounts):
                st.write(f"• **{account}**")
        else:
            st.info("No OAuth accounts saved locally.")
    
    with col2:
        if header_accounts:
            st.subheader("🌐 Saved Header-based Accounts")
            for account in sorted(header_accounts):
                st.write(f"• **{account}**")
        else:
            st.info("No header files saved locally.")
    
    detected_accounts = get_detected_google_accounts()
    if detected_accounts:
        st.subheader("🔍 Detected Google Accounts (in current system)")
        st.info("These accounts can be used to label your authentication setup.")
        for account in detected_accounts:
            source = account.get('source', f"Chrome Profile: {account.get('profile', 'Default')}")
            st.write(f"• **{account['email']} ({account['name']})** - {source}")
    else:
        st.info("No active Google accounts detected on your system/browser.")

def test_search():
    """Streamlit UI for testing search functionality with the authenticated YTMusic object."""
    st.subheader("🔍 Test Search (Requires Authentication)")
    
    if 'ytmusic_object' not in st.session_state or not st.session_state.ytmusic_object:
        st.warning("Please authenticate in the 'Authentication' tab first to enable search.")
        return
    
    search_term = st.text_input(
        "Enter song/artist to search on YouTube Music:",
        placeholder="e.g., 'Bohemian Rhapsody Queen'",
        key="search_input"
    )
    
    if st.button("🔍 Search YouTube Music", key="test_search_button"):
        if search_term:
            with st.spinner("Searching..."):
                try:
                    # Access the YTMusic object from session state
                    results = st.session_state.ytmusic_object.search(search_term, filter="songs", limit=5)
                    
                    st.write(f"**Top 5 search results for '{search_term}':**")
                    
                    if results:
                        for i, result in enumerate(results[:5], 1):
                            title = result.get('title', 'Unknown Title')
                            artist = 'Unknown Artist'
                            if result.get('artists'):
                                artist = result['artists'][0].get('name', 'Unknown Artist')
                            album = result.get('album', {}).get('name', 'Unknown Album')
                            
                            st.write(f"{i}. **{title}** by **{artist}** (Album: {album})")
                    else:
                        st.info("No results found for your search.")
                        
                except Exception as e:
                    st.error(f"Search failed: {e}. Your authentication might be expired. Please re-authenticate.")
                    logger.error(f"Test search failed: {e}", exc_info=True)
        else:
            st.warning("Please enter a search term.")

# ==============================================================================
# Master.py Core Logic Classes (adapted for Streamlit and re-use)
# ==============================================================================

class RateLimiter:
    """Manages API call rate to prevent hitting limits and tracks total calls."""
    def __init__(self, calls_per_second: int = 2):
        self.calls_per_second = calls_per_second
        self.minimum_interval = 1.0 / calls_per_second
        self.last_call_time = 0.0
        # Total API calls is managed by session state to persist across Streamlit re-runs
        # This instance is initialized once and stored in session state.
        
    async def wait(self):
        # Update total API calls in session state
        st.session_state.api_operations += 1 
        current_time = time.time()
        time_since_last_call = current_time - self.last_call_time
        if time_since_last_call < self.minimum_interval:
            await asyncio.sleep(self.minimum_interval - time_since_last_call)
        self.last_call_time = time.time()

class YouTubeMusicHandler:
    """Handles core YouTube Music API interactions (search, create playlist, add to playlist)."""
    def __init__(self, ytmusic_instance: YTMusic, rate_limiter: RateLimiter):
        if ytmusic_instance is None:
            raise ValueError("YTMusic instance must be provided to YouTubeMusicHandler.")
        self.ytmusic = ytmusic_instance
        self.rate_limiter = rate_limiter

    def _sanitize_text(self, text: str) -> str:
        """Sanitizes text for API queries and names."""
        try:
            if not text:
                return ""
            # Ensure text is string, decode if bytes
            if isinstance(text, bytes):
                text = text.decode('utf-8', errors='ignore')
            
            # Normalize unicode characters (e.g., composite characters to single ones)
            normalized = unicodedata.normalize('NFKC', str(text))
            
            # Remove non-printable characters, keep alphanumeric, spaces, and common punctuation
            sanitized = ''.join(char for char in normalized 
                              if char.isprintable() or char.isspace() or char in "!@#$%^&*()-_+=[]{}|;:'\",.<>/?`~")
            
            # Specific replacements for common music metadata quirks
            special_chars = {'–': '-', '—': '-', '“': '"', '”': '"', '’': "'", '‘': "'"}
            for old, new in special_chars.items():
                sanitized = sanitized.replace(old, new)
                
            # Remove multiple spaces
            sanitized = ' '.join(sanitized.split())
                
            return sanitized.strip()
        except Exception as e:
            logger.error(f"Text sanitization failed for '{text}': {e}", exc_info=True)
            return str(text) # Return original text on error, don't crash

    @retry(
        stop=stop_after_attempt(3), # Retry 3 times
        wait=wait_exponential(multiplier=1, min=2, max=5), # Exponential backoff: 2s, 4s (+random), 5s cap
        reraise=True # Re-raise exception after retries exhausted
    )
    async def create_playlist(self, name: str, description: str = "") -> str:
        """Creates a new YouTube Music playlist."""
        await self.rate_limiter.wait() # Wait before making the API call
        sanitized_name = self._sanitize_text(name)
        sanitized_desc = self._sanitize_text(description)
        logger.info(f"Attempting to create playlist: '{sanitized_name}'")
        playlist_id = self.ytmusic.create_playlist(sanitized_name, sanitized_desc)
        logger.info(f"Created playlist ID: {playlist_id}")
        return playlist_id

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=5),
        reraise=True
    )
    async def search_song(self, query: str) -> Optional[List[Dict]]:
        """Searches for a song on YouTube Music."""
        await self.rate_limiter.wait()
        sanitized_query = self._sanitize_text(query)
        logger.info(f"Searching for song: '{sanitized_query}'")
        results = self.ytmusic.search(sanitized_query, filter="songs")
        logger.debug(f"Search results for '{sanitized_query}': {len(results) if results else 0} found.")
        return results[:3] if results else None # Return top 3 results

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=5),
        reraise=True
    )
    async def add_to_playlist(self, playlist_id: str, video_id: str) -> bool:
        """Adds a video to a specific playlist."""
        await self.rate_limiter.wait()
        logger.info(f"Adding video ID '{video_id}' to playlist ID '{playlist_id}'")
        # ytmusicapi.add_playlist_items returns a dict with 'playlistEditResults'
        # It raises an exception on failure
        self.ytmusic.add_playlist_items(playlist_id, [video_id])
        logger.debug(f"Video {video_id} added successfully to {playlist_id}.")
        return True

class PlaylistTransfer:
    """Manages the overall playlist transfer process."""
    def __init__(self, ytmusic_instance: YTMusic):
        # Initialise RateLimiter in session state if not already there, this ensures persistence
        if 'rate_limiter_instance' not in st.session_state:
            st.session_state.rate_limiter_instance = RateLimiter(
                int(os.getenv('RATE_LIMIT_CALLS_PER_SECOND', 2)) # Default 2 calls/second
            )
        self.yt_handler = YouTubeMusicHandler(ytmusic_instance, st.session_state.rate_limiter_instance)

    def _detect_song_column(self, df: pd.DataFrame) -> Optional[str]:
        """Detects the song column based on common headers."""
        possible_headers = ['song', 'songs', 'name', 'names', 'title', 'titles']
        for header_option in possible_headers:
            if header_option in df.columns: # Check for exact match
                return header_option
            # Check for case-insensitive match
            for col in df.columns:
                if col.lower() == header_option:
                    return col
        return None

    async def process_playlist(self, uploaded_csv_file_buffer: io.BytesIO, playlist_name: str, progress_bar, status_text_placeholder) -> Dict:
        """Processes the CSV, searches for songs, and adds them to a new playlist."""
        results = {
            'total_songs': 0,
            'matched_songs': 0,
            'unmatched_songs': [], # List of original song names not found
            'errors': [],          # List of {song_name, error_message}
            'final_df': pd.DataFrame() # DataFrame with updated status
        }

        try:
            # Read CSV from buffer, try UTF-8 then Latin-1
            uploaded_csv_file_buffer.seek(0) # Ensure buffer is at the start
            try:
                df = pd.read_csv(uploaded_csv_file_buffer, encoding='utf-8')
            except UnicodeDecodeError:
                uploaded_csv_file_buffer.seek(0) # Reset again for Latin-1
                df = pd.read_csv(uploaded_csv_file_buffer, encoding='latin-1')
            
            song_column = self._detect_song_column(df)
            if not song_column:
                raise ValueError("Could not find a suitable song column (e.g., 'song', 'songs', 'name', 'names', 'title', 'titles') in your CSV.")

            if df.empty:
                raise ValueError("The uploaded CSV file is empty.")

            # Add/ensure status columns in DataFrame
            if 'Transfer_Status' not in df.columns:
                df['Transfer_Status'] = ''
            if 'Transfer_Date' not in df.columns:
                df['Transfer_Date'] = ''
            if 'YTMusic_URL' not in df.columns:
                df['YTMusic_URL'] = ''
            if 'Error_Details' not in df.columns:
                df['Error_Details'] = ''

            # Create playlist on YouTube Music
            st.write(f"Attempting to create playlist: **`{playlist_name}`**")
            playlist_id = await self.yt_handler.create_playlist(
                playlist_name,
                "Playlist transferred using Streamlit YouTube Music Transfer Tool."
            )
            st.success(f"Playlist '**{playlist_name}**' created successfully on YouTube Music.")
            # Provide a direct link to the newly created playlist:
            st.markdown(f"[View Playlist on YouTube Music](https://music.youtube.com/playlist?list={playlist_id})", unsafe_allow_html=True)
            
            total_songs = len(df)
            results['total_songs'] = total_songs
            status_text_placeholder.text(f"Processing {total_songs} songs...")

            for index, row in df.iterrows():
                progress = (index + 1) / total_songs
                progress_bar.progress(progress) # Update Streamlit progress bar
                
                original_song_name = row[song_column] # Get name from detected column
                status_text_placeholder.text(f"Processing song {index + 1}/{total_songs}: **`{original_song_name}`**")
                logger.info(f"Processing song {index + 1}/{total_songs}: '{original_song_name}'")
                
                try:
                    # Search for the song
                    matches = await self.yt_handler.search_song(original_song_name) 
                    
                    if matches:
                        video_id = matches[0]['videoId'] # Take the first match
                        if not video_id: # Basic check for valid video_id
                             raise ValueError("Video ID not found in search result.")

                        # Add song to playlist
                        await self.yt_handler.add_to_playlist(playlist_id, video_id) 
                        
                        # Update DataFrame for successful transfer
                        df.at[index, 'Transfer_Status'] = 'Success'
                        df.at[index, 'Transfer_Date'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        df.at[index, 'YTMusic_URL'] = f"https://music.youtube.com/watch?v={video_id}"
                        df.at[index, 'Error_Details'] = '' # Clear any previous error
                        
                        results['matched_songs'] += 1
                        logger.info(f"Successfully added: '{original_song_name}'")
                    else:
                        # Update DataFrame for song not found
                        df.at[index, 'Transfer_Status'] = 'Not Found'
                        df.at[index, 'Transfer_Date'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        df.at[index, 'Error_Details'] = 'No matches found on YouTube Music'
                        
                        results['unmatched_songs'].append({
                            'song': original_song_name 
                        })
                        logger.warning(f"Not found: '{original_song_name}'")
                        
                except Exception as e:
                    # Update DataFrame for errors during processing
                    df.at[index, 'Transfer_Status'] = 'Error'
                    df.at[index, 'Transfer_Date'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    df.at[index, 'Error_Details'] = str(e) # Store the error message
                    
                    results['errors'].append({
                        'song': original_song_name,
                        'error': str(e)
                    })
                    logger.error(f"Error processing song '{original_song_name}': {e}", exc_info=True)
                    continue # Continue to next song even if one fails
            
            progress_bar.progress(1.0) # Ensure progress bar hits 100%
            status_text_placeholder.text("Processing complete!")
            results['final_df'] = df # Store the updated DataFrame for display and download
            return results

        except Exception as e:
            st.error(f"Playlist transfer failed: {e}. Please check your CSV or authentication.")
            logger.error(f"Overall playlist transfer failed: {e}", exc_info=True)
            results['final_df'] = df if 'df' in locals() else pd.DataFrame() # Return partial DF on main error
            return results

# ==============================================================================
# Streamlit Tab Pages Functions
# These functions define the content for each tab in the Streamlit app
# ==============================================================================

def auth_tab_content():
    """Content for the Authentication Tab."""
    st.markdown("Use this tab to authenticate with your YouTube Music account. Once authenticated, the status will show in the sidebar, and you can proceed to the 'Playlist Transfer' tab.")

    col_auth_option, col_auth_content = st.columns([0.3, 0.7])

    with col_auth_option:
        auth_page_selection = st.radio(
            "Select Authentication Method:",
            [
                "🔐 OAuth Authentication",
                "🌐 Browser Headers",
                "🍪 Cookie Authentication",
                "📋 List Accounts (and detected)" # Combined listing option
            ],
            key="auth_method_radio"
        )
    
    with col_auth_content:
        # Display the selected authentication setup page
        if auth_page_selection == "🔐 OAuth Authentication":
            setup_oauth_page()
        elif auth_page_selection == "🌐 Browser Headers":
            setup_headers_page()
        elif auth_page_selection == "🍪 Cookie Authentication":
            setup_cookies_page()
        elif auth_page_selection == "📋 List Accounts (and detected)":
            list_accounts_page()
    
    # Test search button is always available in auth tab for immediate verification
    st.markdown("---")
    test_search()

async def playlist_transfer_tab_content():
    """Content for the Playlist Transfer Tab."""
    st.header("Upload CSV & Start Playlist Transfer")
    
    # Check if a YTMusic object is already authenticated in session state
    if 'ytmusic_object' not in st.session_state or not st.session_state.ytmusic_object:
        st.warning("Please authenticate with a YouTube Music account in the 'Authentication' tab first before proceeding.")
        return

    st.success(f"✅ Authenticated as: **{st.session_state.current_account_name}**")
    st.markdown("---")

    uploaded_file = st.file_uploader(
        "Upload your CSV file (e.g., 'song', 'songs', 'name', or 'names' column required)",
        type="csv",
        key="csv_uploader"
    )
    
    playlist_name = st.text_input(
        "Enter desired new YouTube Music playlist name:",
        placeholder="e.g., 'My Awesome Transferred Playlist'",
        key="new_playlist_name_input"
    )

    if uploaded_file and playlist_name:
        if st.button("🚀 Start Playlist Transfer", key="start_transfer_button"):
            # Initialize PlaylistTransfer with the authenticated YTMusic object
            transfer = PlaylistTransfer(st.session_state.ytmusic_object)
            
            with st.spinner("Starting transfer... This may take a while depending on the number of songs. Do not close this tab."):
                progress_bar = st.progress(0) # Progress bar
                status_text_placeholder = st.empty() # Placeholder for dynamic text updates

                # Process the uploaded file in memory
                uploaded_csv_buffer = io.BytesIO(uploaded_file.getvalue())
                
                # Perform the async processing
                results = await transfer.process_playlist(uploaded_csv_buffer, playlist_name, progress_bar, status_text_placeholder)
                
                # After processing, display summary and results
                st.markdown("---")
                st.subheader("Transfer Summary")
                
                if results['final_df'].empty and results['errors']: # If initial DF was empty or early error
                    st.error("Transfer could not be started or failed early. Check logs for details.")
                    if results['errors']:
                        st.json(results['errors']) # Display direct errors
                    return

                total_songs = results['total_songs']
                matched_songs = results['matched_songs']
                unmatched_songs_count = len(results['unmatched_songs'])
                errors_count = len(results['errors'])

                col_summary_1, col_summary_2, col_summary_3, col_summary_4 = st.columns(4)
                with col_summary_1: st.metric("Total Songs", total_songs)
                with col_summary_2: st.metric("Successfully Added", matched_songs)
                with col_summary_3: st.metric("Not Found", unmatched_songs_count)
                with col_summary_4: st.metric("Errors", errors_count)
                
                if unmatched_songs_count > 0:
                    st.write("---")
                    st.subheader("Songs Not Found on YouTube Music:")
                    for song_info in results['unmatched_songs']:
                        st.write(f"- `{song_info['song']}`")
                
                if errors_count > 0:
                    st.write("---")
                    st.subheader("Songs with Errors During Processing:")
                    st.warning("These songs encountered API or processing issues. Review details in the full report.")
                    for error_info in results['errors']:
                        st.write(f"- `{error_info['song']}`: `{error_info['error']}`")
                
                if not results['final_df'].empty:
                    st.write("---")
                    st.subheader("Full Results Table")
                    # Display the updated DataFrame
                    st.dataframe(results['final_df'], use_container_width=True)
                    
                    # Download button for the updated CSV
                    csv_export = results['final_df'].to_csv(index=False, encoding='utf-8').encode('utf-8')
                    st.download_button(
                        label="Download Full Transfer Report (CSV)",
                        data=csv_export,
                        file_name=f"youtube_music_transfer_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                        mime="text/csv",
                    )
                else:
                    st.error("No data processed for the final report. Check initial errors.")
    elif uploaded_file:
        st.info("Please enter a playlist name for your new YouTube Music playlist.")
    else:
        st.info("Please upload your CSV file and enter a playlist name to start the transfer.")

# ==============================================================================
# Main Streamlit App Layout and Entry Point
# ==============================================================================

def main_app():
    """Main Streamlit application layout and logic."""
    
    # Initialize all session state variables. Crucial for persistence across re-runs.
    if 'ytmusic_object' not in st.session_state:
        st.session_state.ytmusic_object = None # Stores the authenticated YTMusic instance
    if 'current_account_name' not in st.session_state:
        st.session_state.current_account_name = "None" # Stores the display name of the authenticated account
    if 'show_new_oauth' not in st.session_state:
        st.session_state.show_new_oauth = False # Flag for showing new OAuth setup UI
    if 'rate_limiter_instance' not in st.session_state:
        st.session_state.rate_limiter_instance = RateLimiter(
            int(os.getenv('RATE_LIMIT_CALLS_PER_SECOND', 2)) # Initialize RateLimiter
        )
    if 'api_operations' not in st.session_state:
        st.session_state.api_operations = 0 # Counter for API calls

    st.title("🎶 YouTube Music Playlist Transfer Tool")
    st.markdown("Easily transfer your song playlists from CSV files to YouTube Music.")

    # Sidebar: Application Info and Status
    st.sidebar.header("📊 Application Info")

    st.sidebar.subheader("API Usage Insights")
    # Display total API calls using st.metric for real-time updates
    st.sidebar.metric("Total API Calls", st.session_state.api_operations)
    
    # Clarification about "tokens" and "cost" for unofficial API
    st.sidebar.markdown("""
    **Note on "Tokens" and "Cost":**
    This tool uses `ytmusicapi`, an unofficial API wrapper for YouTube Music.
    It **does not** involve traditional API "tokens" or direct monetary costs
    like some large language model APIs. The "Total API Calls" metric above
    tracks the number of individual requests made to YouTube Music for operations
    like searching, playlist creations, and song additions.
    """)

    st.sidebar.markdown("---")
    st.sidebar.subheader("📚 Primary Libraries Used")
    st.sidebar.code("""
- **streamlit**: For the interactive web interface.
- **ytmusicapi**: Core library for interacting with YouTube Music API.
- **pandas**: For handling CSV file operations.
- **google-auth***: Google's authentication libraries for OAuth.
- **browser_cookie3***: For retrieving browser cookies for authentication.
- **keyring***: For secure system credential storage and retrieval.
- **tenacity**: For robust retry logic on API calls.
- **aiohttp**: Underlying library used by ytmusicapi for HTTP requests.
- **python-dotenv**: For loading environment variables (e.g., API limits).
(* used for specific authentication methods)
    """)

    st.sidebar.markdown("---")
    st.sidebar.subheader("🤝 Current Authentication Status")
    st.sidebar.info(f"Account: **{st.session_state.current_account_name}**")
    if st.session_state.ytmusic_object:
        st.sidebar.success("Status: Authenticated ✅")
    else:
        st.sidebar.error("Status: Not Authenticated ❌")
        
    st.sidebar.markdown("---")
    st.sidebar.info("Troubleshooting? Check your terminal for detailed logs.")

    # Main content area with tabs for different functionalities
    tab1, tab2 = st.tabs(["🔒 Authentication", "➡️ Playlist Transfer"])

    with tab1:
        auth_tab_content()

    with tab2:
        # Use asyncio.run to execute the async function within Streamlit's sync context.
        # This is generally safe for one-off/button-triggered async operations.
        try:
            import asyncio
            asyncio.run(playlist_transfer_tab_content())
        except RuntimeError as e:
            # This specific error might occur if Streamlit's internal loop is already active
            # and asyncio.run tries to start a new one. It's often handled gracefully by Streamlit.
            if "cannot run an event loop while another loop is running" in str(e):
                logger.warning(f"RuntimeError detected during async call. This is sometimes expected in Streamlit. Error: {e}")
                st.warning("A background process error occurred. Please try again. If it persists, restart the app.")
            else:
                st.error(f"Failed to execute playlist transfer due to a critical error: {e}")
                logger.error(f"Critical error during playlist transfer (async): {e}", exc_info=True)
                
# Entry point for the Streamlit application
if __name__ == "__main__":
    main_app()