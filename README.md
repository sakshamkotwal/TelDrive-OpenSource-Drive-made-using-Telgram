# Teledrive

Teledrive is a self-hostable cloud drive that uses Telegram private channels as the storage backend and pinned messages as the dataset source of truth.

## Features

- **Unlimited Storage**: Uses Telegram's cloud storage.
- **Privacy**: Files are encrypted (AES-GCM) before upload using a client-side key derived from your password.
- **Stateless**: The server is just a relay. All state is in Telegram.
- **Web Interface**: Simple, modern Vue.js frontend.
- **Parallel Upload/Download**: Fast chunked file transfer.

## Architecture

1. **Client**: Vue.js SPA. Handles password input and key derivation.
2. **Server**: FastAPI (Python). Handles chunking, encryption/decryption, and Telegram API communication.
3. **Storage**: Telegram Private Channel.
   - **Files**: Stored as a sequence of "Document" messages.
   - **Metadata**: Stored as a JSON file pinned in the channel.

## Deployment

### Prerequisites

1. **Telegram Bot**:
   - Talk to [@BotFather](https://t.me/BotFather) to create a new bot.
   - Get the **Bot Token**.

2. **Hosting**:
   - You can host this on **Render**, **Railway**, **Fly.io**, or any VPS.
   - Dockerfile is not provided but the app is standard Python.

### Environment Variables

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | **Required**. The token from BotFather. |
| `SECRET_KEY` | Secret key for server-side JWT signing (generate a random string). |
| `PORT` | Port to run on (default 8000). |

### Running Locally

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Run the server:
   ```bash
   export TELEGRAM_BOT_TOKEN="your_token_here"
   uvicorn teledrive.backend.main:app --reload
   ```

3. Open `http://localhost:8000`.

### First Run Setup

1. Open the app in your browser.
2. It will ask for a **Channel ID**.
3. Create a **New Private Channel** in Telegram.
4. **Add your Bot** to the channel as an **Administrator** (needed to pin messages).
5. Send any message to the channel and forward it to [@userinfobot](https://t.me/userinfobot) (or check URL) to get the Channel ID (usually starts with `-100`).
6. Enter the Channel ID in Teledrive.
7. Set a **Password**. This password is used to encrypt your files. **Do not lose it.**

## Security Model

- **Files**: Encrypted using AES-256-GCM. The key is derived from your password using PBKDF2. The server does not persist the password or the key (it's kept in memory for the request duration via a stateless token/header mechanism).
- **Metadata**: The directory structure is stored in the Telegram channel as a JSON file. Currently, metadata is **not encrypted** to allow for server-side search and listing, but file contents are.
- **Access Control**: Access is restricted to those who know the Channel ID and the Password.

## Disclaimer

This project is for educational purposes. Use at your own risk. Telegram may ban bots that abuse their API for mass storage.
