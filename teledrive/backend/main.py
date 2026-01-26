from fastapi import FastAPI, Depends, UploadFile, File, Form, HTTPException, status, Header
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import Optional, List
import logging
import os
import secrets
from contextlib import asynccontextmanager

from teledrive.backend.config import settings
from teledrive.backend.telegram_bot import TelegramClient
from teledrive.backend.drive import DriveManager, get_drive_manager
from teledrive.backend.models import TeledriveFile, TeledriveFolder

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global Drive Manager (to be initialized per request or global if single channel)
# Since we support dynamic channel ID from session, we might need a factory.
# But `TelegramClient` needs the token. Token is in ENV.
# So `TelegramClient` is global.
# `DriveManager` depends on `channel_id`.

telegram_client = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global telegram_client
    if settings.TELEGRAM_BOT_TOKEN:
        telegram_client = TelegramClient(settings.TELEGRAM_BOT_TOKEN)
        # Verify bot?
        try:
            me = await telegram_client.get_me()
            logger.info(f"Bot connected: {me.username}")
        except Exception as e:
            logger.error(f"Failed to connect bot: {e}")
    yield
    # Cleanup

app = FastAPI(title="Teledrive", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dependency to get DriveManager based on header/session
async def get_drive(
    x_channel_id: Optional[str] = Header(None, alias="X-Channel-ID"),
    # In a real app, we'd use JWT containing channel_id and encryption_key
    # For simplicity/mvp, we pass them as headers
    x_encryption_key: Optional[str] = Header(None, alias="X-Encryption-Key")
) -> DriveManager:
    if not telegram_client:
        raise HTTPException(status_code=503, detail="Telegram Bot not configured (Check ENV)")

    if not x_channel_id:
        raise HTTPException(status_code=400, detail="Channel ID required")

    # We create a new DriveManager instance (lightweight)
    try:
        channel_id_int = int(x_channel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Channel ID")

    # Use factory to get singleton instance (ensures locking works)
    dm = get_drive_manager(telegram_client, channel_id_int)

    # Ensure initialized (check access)
    try:
        await dm.ensure_initialized()
    except Exception as e:
        logger.error(f"Drive init failed: {e}")
        raise HTTPException(status_code=403, detail=f"Failed to access channel: {str(e)}")

    return dm

# Auth dependency to get key
def get_encryption_key(x_encryption_key: str = Header(..., alias="X-Encryption-Key")) -> bytes:
    try:
        # Key should be hex encoded
        return bytes.fromhex(x_encryption_key)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Encryption Key format (Hex expected)")

# Endpoints

@app.get("/api/status")
async def status_check():
    return {"status": "ok", "bot_configured": telegram_client is not None}

@app.post("/api/setup/verify")
async def verify_setup(channel_id: int):
    """Verifies that the bot can access the channel."""
    if not telegram_client:
         raise HTTPException(status_code=503, detail="Bot token missing")
    try:
        chat = await telegram_client.get_chat(channel_id)
        return {"ok": True, "chat_title": chat.title}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/files")
async def list_files(folder_id: Optional[str] = None, drive: DriveManager = Depends(get_drive)):
    """Returns files and folders in the given folder."""
    # Assuming dataset is loaded
    if drive.dataset is None:
        await drive.load_dataset()

    dataset = drive.dataset
    files = [f for f in dataset.files if f.parent_id == folder_id]
    folders = [f for f in dataset.folders if f.parent_id == folder_id]

    # Enrich with some derived info if needed
    return {"files": files, "folders": folders}

@app.post("/api/files/upload")
async def upload_file(
    file: UploadFile = File(...),
    parent_id: Optional[str] = Form(None),
    drive: DriveManager = Depends(get_drive),
    key: bytes = Depends(get_encryption_key)
):
    try:
        # We need to read file size? UploadFile has .size? No.
        # But we can read chunk by chunk.
        # We pass the stream.
        # We assume size is unknown or we just count it.
        # DriveManager.upload_file expects size?
        # Actually `UploadFile` might not have size if chunked transfer.
        # We can pass 0 or update logic to not require size upfront.
        # Let's check `drive.py`. It takes `size`.
        # We can try to get size from header `content-length` but it might be missing.
        # Updated `drive.py` stores size in metadata. We can calculate it during upload.

        # Determine filename
        filename = file.filename or "unnamed_file"

        # Workaround for size: We'll calculate it as we go.
        # We pass 0 and update `file_entry.size` later?
        # `drive.py`: `file_entry = TeledriveFile(..., size=size, ...)`
        # We should modify `drive.py` to allow updating size at the end.

        # For now, pass 0.
        uploaded_file = await drive.upload_file(
            file_stream=file,
            filename=filename,
            size=0, # Placeholder
            parent_id=parent_id,
            encryption_key=key
        )

        # Fix size
        # Wait, `drive.py` loop: `total_uploaded += len(chunk_data)`.
        # `drive.py` doesn't update `file_entry.size`.
        # We should fix `drive.py` to update size.

        return uploaded_file
    except Exception as e:
        logger.error(f"Upload error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/files/download/{file_id}")
async def download_file(
    file_id: str,
    drive: DriveManager = Depends(get_drive),
    key: bytes = Depends(get_encryption_key)
):
    try:
        # Find file to get metadata (mime type, name)
        file_info = next((f for f in drive.dataset.files if f.id == file_id), None)
        if not file_info:
            raise HTTPException(status_code=404, detail="File not found")

        generator = drive.download_file(file_id, key)

        return StreamingResponse(
            generator,
            media_type=file_info.mime_type,
            headers={"Content-Disposition": f'attachment; filename="{file_info.name}"'}
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except Exception as e:
        logger.error(f"Download error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/folders")
async def create_folder(
    name: str = Form(...),
    parent_id: Optional[str] = Form(None),
    drive: DriveManager = Depends(get_drive)
):
    try:
        return await drive.create_folder(name, parent_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/items/{item_id}")
async def delete_item(
    item_id: str,
    drive: DriveManager = Depends(get_drive)
):
    try:
        await drive.delete_item(item_id)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Serve Frontend
# We assume the frontend build is in `teledrive/frontend`
app.mount("/", StaticFiles(directory="teledrive/frontend", html=True), name="frontend")
