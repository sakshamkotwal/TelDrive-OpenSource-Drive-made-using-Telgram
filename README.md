# Teledrive

Teledrive is a production-ready, self-hostable cloud drive that turns **Telegram Private Channels** into unlimited, secure storage. It treats pinned messages as the file system's "source of truth", ensuring that your data remains portable and resilient.

## 🚀 Features

*   **Unlimited Storage**: Leverages Telegram's cloud.
*   **Virtual File System**: Supports nested folders, renaming, and moving files (metadata-only operations).
*   **Secure**:
    *   **AES-256-GCM Encryption**: Files are encrypted before upload.
    *   **Stateless Server**: No database required. The server is a pass-through relay.
    *   **Private**: Data resides only in your private Telegram channel.
*   **High Performance**:
    *   **Parallel Downloads**: Fetches multiple file chunks simultaneously.
    *   **Chunked Uploads**: Handles large files efficiently.
*   **Modern UI**:
    *   **Mobile-First Design**: Responsive, dark-themed interface (Google Drive inspired).
    *   **Real-time Feedback**: Toast notifications and progress updates.
    *   **Search**: Full-text search across your drive.

## 🛠 Architecture

1.  **Frontend**: Vanilla JS / Vue-like structure (lightweight). Handles key derivation and UI logic.
2.  **Backend**: FastAPI (Python). Orchestrates chunking, encryption, and Telegram interactions.
3.  **Storage Layer**:
    *   **Telegram Private Channel**: The physical storage.
    *   **Pinned Message**: The "File Allocation Table" (JSON dataset containing file paths, IDs, and metadata).

## 📦 Deployment

Teledrive is designed to be stateless, making it perfect for platforms like **Render**, **Railway**, or **Fly.io**.

### Environment Variables

| Variable | Description |
| :--- | :--- |
| `TELEGRAM_BOT_TOKEN` | **Required**. Token from [@BotFather](https://t.me/BotFather). |
| `SECRET_KEY` | **Required**. A random string for securing sessions. |
| `PORT` | Optional. Defaults to `8000`. |
| `LOG_LEVEL` | Optional. Defaults to `INFO` (set to `DEBUG` for verbose logs). |

### Option A: Docker (Recommended)

The repository includes a production-ready `Dockerfile`.

```bash
docker build -t teledrive .
docker run -p 8000:8000 -e TELEGRAM_BOT_TOKEN="xxx" -e SECRET_KEY="xxx" teledrive
```

### Option B: Render.com

1.  Create a new **Web Service**.
2.  Connect your repository.
3.  Select **Docker** as the Runtime (or Python 3).
4.  Set Environment Variables (`TELEGRAM_BOT_TOKEN`, `SECRET_KEY`).
5.  Deploy.

### Option C: Manual / VPS

1.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```
2.  Run the server (using Procfile or direct command):
    ```bash
    uvicorn teledrive.backend.main:app --host 0.0.0.0 --port 8000
    ```

## ⚡ First-Time Setup

1.  **Create a Telegram Bot**: Message [@BotFather](https://t.me/BotFather) to create a bot and get the token.
2.  **Create a Private Channel**: Create a new Channel in Telegram.
3.  **Add Bot as Admin**: Add your bot to the channel with "Post Messages", "Edit Messages", and "Pin Messages" permissions.
4.  **Get Channel ID**:
    *   Post a message in the channel.
    *   Forward it to [@userinfobot](https://t.me/userinfobot) (or look at the link in Telegram Web).
    *   The ID usually looks like `-100xxxxxxxxxx`.
5.  **Launch Teledrive**: Open your deployed URL.
6.  **Onboard**: Enter the Channel ID and choose a strong **Encryption Password**.

> **Note**: Your encryption password is **never stored** on the server. If you lose it, you lose access to your files.

## 🛡 Security Model

*   **Encryption**: File content is encrypted/decrypted on the server using a key derived from your password. The key exists in memory only during the request.
*   **Metadata**: Directory structure is stored as a pinned JSON document in the channel.
*   **Resilience**: The system uses atomic updates for the dataset. If an update fails, it rolls back or creates a new pinned checkpoint.

## ❓ Troubleshooting

### "Channel ID Invalid" or "Bot cannot access channel"
*   Ensure the bot is an **Administrator** in the channel.
*   Ensure the Channel ID starts with `-100`.
*   Try sending a message to the channel from the bot manually to verify permissions.

### "Decryption Failed"
*   This usually means you entered the wrong **Encryption Password**.
*   Teledrive cannot recover lost passwords. If lost, your files are permanently inaccessible.

### "Upload Failed"
*   Check your internet connection.
*   Large files (>2GB) are split into chunks. If one chunk fails, the upload aborts.
*   Ensure your Telegram Bot Token is valid.

## ⚠️ Disclaimer

This project is open-source software. Users are responsible for complying with Telegram's Terms of Service.
