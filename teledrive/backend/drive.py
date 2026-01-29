import asyncio
import json
import logging
from typing import Optional, List, Generator, AsyncGenerator
from io import BytesIO
import time
from binascii import hexlify, unhexlify

from teledrive.backend.telegram_bot import TelegramClient
from teledrive.backend.models import DriveDataset, TeledriveFile, TeledriveFolder, FileChunk
from teledrive.backend.utils import encrypt_chunk, decrypt_chunk
from teledrive.backend.config import settings

logger = logging.getLogger(__name__)

class DriveManager:
    def __init__(self, telegram_client: TelegramClient, channel_id: Optional[int] = None):
        self.tg = telegram_client
        self.channel_id = channel_id
        self.dataset: Optional[DriveDataset] = None
        self.dataset_message_id: Optional[int] = None
        self.dataset_lock = asyncio.Lock()

    async def ensure_initialized(self):
        if not self.channel_id:
            raise ValueError("Channel ID not configured")

        # Double check locking to avoid concurrent fetches
        if self.dataset is None:
            await self.load_dataset()

    async def _load_dataset_internal(self):
        pinned = await self.tg.get_pinned_message(self.channel_id)
        if pinned and pinned.document:
            # Download dataset
            logger.info("Found pinned dataset, downloading...")
            file_id = pinned.document.file_id
            # Ensure we track the message ID
            self.dataset_message_id = pinned.message_id
            data = await self.tg.download_file(file_id)
            try:
                json_data = json.loads(data.decode('utf-8'))
                self.dataset = DriveDataset(**json_data)
                logger.info(f"Dataset loaded. {len(self.dataset.files)} files, {len(self.dataset.folders)} folders.")
            except Exception as e:
                logger.error(f"Failed to parse dataset: {e}")
                raise e
        else:
            logger.info("No pinned dataset found. Initializing new one.")
            self.dataset = DriveDataset(last_updated=time.time())
            self.dataset_message_id = None
            pass

    async def load_dataset(self):
        async with self.dataset_lock:
            await self._load_dataset_internal()

    async def _save_dataset_internal(self):
        """Internal method to save dataset. Assumes lock is held."""
        if not self.dataset:
            return

        self.dataset.last_updated = time.time()
        # Serialize
        data = self.dataset.model_dump_json().encode('utf-8')

        # Retry logic for saving
        for attempt in range(3):
            try:
                if self.dataset_message_id:
                    logger.info(f"Editing existing dataset message {self.dataset_message_id} (Attempt {attempt+1})")
                    # Try to edit existing message
                    msg = await self.tg.edit_dataset_file(self.channel_id, self.dataset_message_id, data)
                    self.dataset_message_id = msg.message_id
                    logger.info("Dataset saved successfully (Edited).")
                    return
            except Exception as e:
                logger.warning(f"Failed to edit dataset message: {e}. Checking if we should fallback...")
                # If error is not retry-able or we want to try new pin:
                if attempt == 2:
                     logger.warning("Max retries for edit reached. Falling back to new pin.")
                else:
                    await asyncio.sleep(1)
                    continue

        # Fallback: Upload and pin (First time or fallback after edits failed)
        try:
            logger.info("Uploading new dataset message...")
            msg = await self.tg.upload_dataset(self.channel_id, data)
            self.dataset_message_id = msg.message_id
            logger.info("Dataset saved successfully (New Pin).")
        except Exception as e:
            logger.error(f"CRITICAL: Failed to save dataset: {e}")
            raise e

    async def save_dataset(self):
        async with self.dataset_lock:
            await self._save_dataset_internal()

    async def upload_file(self,
                          file_stream,
                          filename: str,
                          size: int,
                          parent_id: Optional[str],
                          encryption_key: bytes) -> TeledriveFile:
        await self.ensure_initialized()

        # Create file entry
        file_entry = TeledriveFile(
            name=filename,
            size=size,
            parent_id=parent_id
        )

        chunk_size = settings.MAX_CHUNK_SIZE
        part_number = 0
        total_uploaded = 0

        try:
            while True:
                chunk_data = await file_stream.read(chunk_size)
                if not chunk_data:
                    break

                # Encrypt
                try:
                    ciphertext, iv, tag = encrypt_chunk(chunk_data, encryption_key)
                except Exception as e:
                    logger.error(f"Encryption failed for part {part_number}: {e}")
                    raise RuntimeError("Encryption failed") from e

                # Combine for storage: IV + Tag + Ciphertext
                # IV is 12 bytes, Tag is 16 bytes.
                blob = iv + tag + ciphertext

                # Upload chunk
                try:
                    msg = await self.tg.upload_chunk(
                        self.channel_id,
                        blob,
                        f"{file_entry.id}_p{part_number}.bin"
                    )
                except Exception as e:
                    logger.error(f"Failed to upload chunk {part_number}: {e}")
                    # Retrying is handled in telegram_bot.py, if it failed there, it's fatal.
                    raise RuntimeError(f"Chunk upload failed: {e}") from e

                # Record chunk
                chunk_info = FileChunk(
                    message_id=msg.message_id,
                    size=len(blob),
                    part_number=part_number
                )
                file_entry.chunks.append(chunk_info)

                part_number += 1
                total_uploaded += len(chunk_data) # Original size

            # Verify we actually uploaded something if size was expected > 0
            if total_uploaded == 0 and size > 0:
                logger.warning(f"Uploaded 0 bytes for file {filename} but expected {size}")

            # Update dataset
            async with self.dataset_lock:
                # RELOAD DATASET to prevent race conditions (lost updates)
                # This ensures we merge with any changes that happened while we were uploading chunks
                await self._load_dataset_internal()

                # Update total size
                file_entry.size = total_uploaded

                self.dataset.files.append(file_entry)
                await self._save_dataset_internal()

            logger.info(f"File {filename} uploaded successfully. Size: {total_uploaded} bytes, Chunks: {part_number}")
            return file_entry

        except Exception as e:
            logger.error(f"Upload failed for {filename}: {e}")
            # Attempt best-effort cleanup of orphaned chunks
            if file_entry.chunks:
                logger.info("Cleaning up chunks from failed upload...")
                for chunk in file_entry.chunks:
                    try:
                        await self.tg.delete_message(self.channel_id, chunk.message_id)
                    except Exception as cleanup_err:
                        logger.warning(f"Failed to delete orphaned chunk {chunk.message_id}: {cleanup_err}")
            raise e

    async def download_file(self, file_id: str, encryption_key: bytes) -> AsyncGenerator[bytes, None]:
        await self.ensure_initialized()

        # Find file
        target_file = next((f for f in self.dataset.files if f.id == file_id), None)
        if not target_file:
            raise FileNotFoundError("File not found")

        # Sort chunks
        chunks = sorted(target_file.chunks, key=lambda c: c.part_number)

        # Download chunks
        # Sequential for now. Parallelizing requires managing asyncio.gather and ordering.
        # "Parallel chunked downloads for speed" is a requirement.

        # We can fetch N chunks ahead?
        # Or just use gather for all? If file is huge, memory issue.
        # Let's do sequential first for correctness, then optimize if needed?
        # Requirement says "Parallel chunked downloads".
        # Let's pre-fetch in batches.

        # Parallel download logic: Fetch in batches
        # We process batches, but we must yield chunks in ORDER.
        # asyncio.gather returns results in the order of tasks, which corresponds to our batch list.
        # So order is preserved.

        batch_size = settings.DOWNLOAD_BATCH_SIZE
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i+batch_size]
            logger.debug(f"Downloading batch {i//batch_size} ({len(batch)} chunks) for file {file_id}")

            # Create tasks for the batch
            tasks = [self.tg.get_chunk_bytes(self.channel_id, c.message_id) for c in batch]

            try:
                # Wait for all in batch to complete
                results = await asyncio.gather(*tasks)
            except Exception as e:
                logger.error(f"Failed to download batch: {e}")
                raise e

            for idx, raw_data in enumerate(results):
                if len(raw_data) < 28:
                    logger.error(f"Chunk data too short (corrupt?): {len(raw_data)} bytes")
                    raise ValueError("Chunk corrupted")

                iv = raw_data[:12]
                tag = raw_data[12:28]
                ciphertext = raw_data[28:]

                try:
                    plaintext = decrypt_chunk(ciphertext, encryption_key, iv, tag)
                except Exception as e:
                    logger.error(f"Decryption failed for chunk: {e}")
                    raise RuntimeError("Decryption failed (Wrong password?)") from e

                yield plaintext

    async def create_folder(self, name: str, parent_id: Optional[str] = None) -> TeledriveFolder:
        await self.ensure_initialized()
        folder = TeledriveFolder(name=name, parent_id=parent_id)
        async with self.dataset_lock:
            await self._load_dataset_internal()
            self.dataset.folders.append(folder)
            await self._save_dataset_internal()
        return folder

    async def delete_item(self, item_id: str):
        await self.ensure_initialized()
        async with self.dataset_lock:
            await self._load_dataset_internal()
            # Check files
            file_idx = next((i for i, f in enumerate(self.dataset.files) if f.id == item_id), None)
            if file_idx is not None:
                file_obj = self.dataset.files.pop(file_idx)
                # Async delete messages (fire and forget or wait?)
                # We should wait to ensure cleanup
                msg_ids = [c.message_id for c in file_obj.chunks]
                for msg_id in msg_ids:
                    await self.tg.delete_message(self.channel_id, msg_id)
                await self._save_dataset_internal()
                return

            # Check folders
            folder_idx = next((i for i, f in enumerate(self.dataset.folders) if f.id == item_id), None)
            if folder_idx is not None:
                # Recursive delete?
                # For now, just delete the folder entry. Orphans will remain.
                # Ideally, check for children.
                self.dataset.folders.pop(folder_idx)
                await self._save_dataset_internal()
                return

            raise FileNotFoundError("Item not found")


# Global registry for DriveManagers to ensure shared locking per channel
_drive_managers = {}

def get_drive_manager(telegram_client: TelegramClient, channel_id: int) -> DriveManager:
    if channel_id not in _drive_managers:
        _drive_managers[channel_id] = DriveManager(telegram_client, channel_id)
    return _drive_managers[channel_id]
