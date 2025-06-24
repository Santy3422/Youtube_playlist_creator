**YouTube Music Playlist Creator**
_Description_
An AI-powered automation tool that helps users create and manage YouTube playlists efficiently. 
The tool allows users to bulk import songs from CSV files and automatically create/update YouTube playlists using both YouTube Data API and YouTube Music API.

**Features**
_🔐 Multiple Authentication Methods:_
- YouTube Data API (OAuth)
- API Key (Search Only)

_📂 CSV File Support:_
- Auto-detection of song columns
- Support for Spotify playlist exports
- Preview and validation of song data

_🎵 Playlist Management:_
- Create new playlists
- Add to existing playlists
- Batch processing of songs
- Privacy control (Private/Unlisted/Public)

_📊 Real-time Statistics:_
- Search counts
- Success/failure rates
- API quota usage tracking
- Cost estimation

**Prerequisites**


# Required Python packages
streamlit>=1.24.0
pandas>=1.5.3
google-api-python-client>=2.108.0
google-auth-oauthlib>=1.2.0
python-dotenv>=1.0.0
ytmusicapi>=1.0.0

**Setup Instructions**
1. Clone the repository:
git clone https://github.com/yourusername/youtube-playlist-creator.git
cd youtube-playlist-creator

2. Install dependencies:
pip install -r requirements.txt

3. Configure Authentication:
- Create a Google Cloud Project
- Enable YouTube Data API v3
- Create OAuth 2.0 credentials
- Download credentials.json
**Set environment variables:**
YOUTUBE_API_KEY=your_api_key
YOUTUBE_CLIENT_ID=your_client_id
YOUTUBE_CLIENT_SECRET=your_client_secret
YOUTUBE_REDIRECT_URIS=http://localhost:8501/,http://localhost:8080/

4. Run the application:
streamlit run youtube_manager.py

**Usage Guide**
1. Authentication:
- Choose authentication method (OAuth/API Key)
- Follow the browser prompts to authenticate
- Verify connection status

2. Upload Songs:
- Prepare CSV file with song names
- Upload using the file uploader
- Verify song column detection

3. Create Playlist:
- Enter playlist name
- Select privacy setting
- Configure processing options
- Click "Create YouTube Music Playlist"

4. Monitor Progress:
- Watch real-time processing
- Check success/failure rates
- View detailed results
- Access created playlist

**Features Details**
_Authentication_
- Secure OAuth 2.0 flow
- Token persistence and refresh
- Multiple authentication methods
- Session management

_CSV Processing_
- Automatic column detection
- Data cleaning and validation
- Batch processing support
- Progress tracking

_Playlist Management_
- Create new playlists
- Add to existing playlists
- Privacy controls
- Quota management

_Monitoring_
- Real-time statistics
- API quota tracking
- Error logging
- Performance metrics
  
**API Quotas and Limits**
- Daily API quota: 10,000 units
- Search cost: 100 units/request
- Playlist insertion: 50 units/request
- Max playlist size: 5,000 songs

**Error Handling**
- Automatic retries
- Rate limiting protection
- Error logging
- User-friendly error messages
 
**Security Features**
- Secure token storage
- Session management
- Rate limiting
- Quota monitoring
  
**Contributing**
1. Fork the repository
2. Create your feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

_**License**_
This project is licensed under the MIT License - see the LICENSE file for details.

**Acknowledgments**
- YouTube Data API
- YouTube Music API
- Streamlit Framework
- Google Cloud Platform

**Support**
For issues and feature requests, please use the GitHub Issues section.

**Disclaimer**
This tool is not affiliated with YouTube or Google. Use responsibly and in accordance with YouTube's terms of service.
