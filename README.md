# YouTube Music Automation Agent 🎵

A robust Streamlit-based tool to automate the creation and management of YouTube Music playlists using the YouTube Data API and YTMusic API.

---

## 🚀 Features

- **Create new playlists** or **add songs to existing playlists** on YouTube Music.
- **Smart duplicate detection** using fuzzy matching and normalization.
- **Batch processing** (up to 1200 songs per batch).
- **Quota management**: Prevents exceeding YouTube API daily limits.
- **Detailed CSV reports**: Download full, success-only, or failure-only logs.
- **Real-time progress and stats** in the sidebar.
- **OAuth authentication** with Google for secure access.
- **Comprehensive error handling** and logging.
- **Session state** for smooth multi-step workflows.

---

## 🖥️ Requirements

- Python 3.8+
- [Google Cloud Project](https://console.cloud.google.com/) with YouTube Data API enabled
- credentials.json (OAuth client secrets) in the project directory

### Python Packages

- `streamlit`
- `pandas`
- `numpy`
- `ytmusicapi`
- `google-api-python-client`
- `google-auth-oauthlib`
- `python-dotenv`
- `rapidfuzz`

Install all dependencies:
```sh
pip install -r requirements.txt
```

---

## ⚡ Quick Start

1. **Clone this repo** and `cd` into the project folder.
2. **Add your credentials.json** (from Google Cloud Console) to the project directory.
3. **Run the app:**
    ```sh
    streamlit run youtube_manager.py
    ```
4. **Authenticate** with your Google account when prompted.
5. **Upload your CSV** of songs (column name can be `song`, `title`, `track`, etc.).
6. **Choose to create a new playlist or add to an existing one.**
7. **Download the detailed CSV report** after processing.

---

## 📝 CSV Format

Your CSV should have a column with song names.  
Supported column names: `song`, `title`, `track`, `name`, etc.

Example:
```csv
song
Blinding Lights
Shape of You
永遠に光れ (Everlasting Shine)
```

---

## 🛡️ Quota Management

- **Daily quota is set to 210,000 tokens** (configurable in the code).
- Each search: 100 tokens, each playlist insert: 50 tokens, playlist create: 50 tokens.
- The app will halt processing if a batch would exceed your daily quota.

---

## 📊 Sidebar Stats

- Estimated tokens needed for your operation
- Searches, songs added, errors, playlists created, duplicates skipped
- Quota used and remaining (local estimate)
- Progress bar for quota usage

---

## 🐞 Troubleshooting

- **Authentication errors:** Ensure credentials.json is present and valid. Clear tokens if needed.
- **Quota errors:** Wait for quota reset (usually every 24h) or reduce batch size.
- **API errors:** Check your internet connection and Google Cloud API status.

---

## 🧪 Testing

- Manual testing is recommended for authentication, playlist creation, and error handling.
- For automated testing, refactor the agent logic into a separate module and use `unittest` or `pytest` with mocks.

---

## ⚠️ Disclaimer

- **Do not share your credentials.json or token files.**
- This tool is for personal use. Respect YouTube’s terms of service and API quotas.

---

## 📄 License

MIT License

---

## 🙏 Credits

- [Google API Python Client](https://github.com/googleapis/google-api-python-client)
- [YTMusicAPI](https://github.com/sigma67/ytmusicapi)
- [Streamlit](https://streamlit.io/)

---

**Happy playlisting! 🎶**

---

Let me know if you want a .gitignore or a sample requirements.txt!

Support For issues and feature requests, please use the GitHub Issues section.

Disclaimer This tool is not affiliated with YouTube or Google. Use responsibly and in accordance with YouTube's terms of service.
