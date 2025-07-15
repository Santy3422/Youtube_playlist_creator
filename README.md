# YouTube Music Automation Agent 🎵

A robust, production-grade tool built with **Streamlit** to automate the creation and management of **YouTube Music playlists** using the **YouTube Data API v3** and **YTMusic API**.

---

## 🚀 Features

- Create **new playlists** or **add songs to existing** ones on YouTube Music.
- Smart duplicate detection using **fuzzy matching**, **normalization**, and **video ID history**.
- Supports **quick mode** (fast search) and **robust mode** (with ambiguity detection).
- Real-time **quota tracking**, with daily limits and per-song cost calculations.
- Per-song processing with isolated 10-second timeouts to improve reliability.
- Handles up to **1200+ songs** in one batch using efficient session and API handling.
- Google OAuth 2.0 authentication with token refresh and multi-account support.
- **Detailed CSV report downloads**: full log, successes, failures, and duplicates.
- Real-time **progress bar** and actionable stats in the sidebar.
- Clean logging system with persistent file-based debug logs.

---

## 🖥️ Requirements

- **Python** 3.8+
- **Google Cloud Project** with YouTube Data API v3 enabled
- `credentials.json` (OAuth client secret) in the project root

### Python Packages

- `streamlit`
- `pandas`
- `numpy`
- `youtube data api V3`
- `google-api-python-client`
- `google-auth-oauthlib`
- `python-dotenv`
- `rapidfuzz`

Install all dependencies:
```sh
```

---

# YouTube Music Automation Agent 🎵

A robust, production-grade tool built with **Streamlit** to automate the creation and management of **YouTube Music playlists** using the **YouTube Data API v3** and **YTMusic API**.

---

## 🚀 Features

- **Create new playlists or add songs to existing ones** on YouTube Music.
- **Smart duplicate detection** using fuzzy matching, normalization, and video ID history.
- **Quick mode** (fast search) and **robust mode** (with ambiguity detection).
- **Real-time quota tracking** with daily limits and per-song cost calculations.
- **Per-song processing** with isolated 10-second timeouts to improve reliability.
- **Handles up to 1200+ songs** in one batch using efficient session and API handling.
- **Google OAuth 2.0 authentication** with token refresh and multi-account support.
- **Detailed CSV report downloads:** full log, successes, failures, and duplicates.
- **Real-time progress bar** and actionable stats in the sidebar.
- **Clean logging system** with persistent file-based debug logs.

---

## 🖥️ Requirements

- **Python 3.8+**
- **Google Cloud Project** with YouTube Data API v3 enabled
- **credentials.json** (OAuth client secret) in the project root

### Python Packages

Install required packages:
```bash
pip install -r requirements.txt
```

Main packages: `streamlit`, `google-api-python-client`, `google-auth-oauthlib`, `ytmusicapi`, `rapidfuzz`, `pandas`, `numpy`, `python-dotenv`

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
**Supported column names:** `song`, `title`, `track`, `name`, `songs`  
**Optional columns:** `artist`, `album` (improves match accuracy)

Example:
```csv
song,artist
Blinding Lights,The Weeknd
Shape of You,Ed Sheeran
永遠に光れ (Everlasting Shine),Aimer
```

---

## 🛡️ Quota Management

- **Daily quota is set to 200,000 tokens** (configurable in quota_settings).
- **Each search:** 100 tokens, **each playlist insert:** 50 tokens, **playlist create:** 50 tokens.
- **The app will halt processing** if a batch would exceed your daily quota.
- **Sidebar stats show** live quota usage and estimated songs remaining.

---

## 📊 Sidebar Stats

- **Estimated tokens per song** (based on mode)
- **Tokens used vs daily quota**
- **Estimated songs remaining**
- **Songs added, skipped, duplicates, and failures**
- **Progress bar and real-time batch status**

---

## 🐞 Troubleshooting

- **Authentication errors:** Ensure credentials.json is present and valid. Delete token.json and re-authenticate if token fails.
- **Quota errors:** Wait for quota reset (usually every 24h) or reduce batch size.
- **API errors:** Check your internet connection and Google Cloud API status.
- **Blank Streamlit screen:** Ensure the main block contains proper asyncio.run(main()) setup.

---

## 🧪 Testing

- **Manual testing is recommended** for CSV upload, OAuth login, playlist creation, and error handling.
- **For automated testing,** move agent logic to a separate file (e.g. agent.py) and use pytest with unittest.mock to simulate API calls.

---

## ⚠️ Disclaimer

- **This tool is intended for educational and personal use only.**
- **Do not share your credentials.json or token files.**
- **You are solely responsible** for adhering to YouTube's API Terms of Service and Google's policies.
- **This tool is not affiliated** with Google or YouTube.
- Use responsibly and in accordance with **YouTube's terms of service**.

---

## 📄 License

MIT License

---

## 🙏 Credits

- [Google API Python Client](https://github.com/googleapis/google-api-python-client)
- [YTMusicAPI](https://github.com/sigma67/ytmusicapi)
- [Streamlit](https://streamlit.io)
- [RapidFuzz](https://github.com/maxbachmann/RapidFuzz)

---

**Happy playlisting! 🎶**

---

For issues, feedback, or feature requests, please use the [GitHub Issues](https://github.com/your-username/youtube-music-agent/issues) section.
```

Support For issues and feature requests, please use the GitHub Issues section.

