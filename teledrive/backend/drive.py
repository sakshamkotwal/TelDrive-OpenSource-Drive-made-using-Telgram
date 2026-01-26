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
            # We don't save immediately here to avoid circular logic or extra calls,
            # but if it's new, we should probably save it eventually.
            # But caller usually modifies and saves.
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

        # Upload and pin
        # We need to unpin the old one? send_document(..., pin=True) isn't atomic replace.
        # But we can just pin the new one, and maybe clean up old ones later.
        # telegram_bot.upload_dataset does upload + pin.

        # Note: If we have the old pinned message ID, we could delete it after successful upload.
        # But we don't track it explicitly in memory yet (except via load).
        # Optimization: Track pinned message ID in DriveManager?

        await self.tg.upload_dataset(self.channel_id, data)
        # TODO: Delete old pinned message to keep channel clean

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
                ciphertext, iv, tag = encrypt_chunk(chunk_data, encryption_key)

                # Combine for storage: IV + Tag + Ciphertext
                # IV is 12 bytes, Tag is 16 bytes.
                blob = iv + tag + ciphertext

                # Upload chunk
                msg = await self.tg.upload_chunk(
                    self.channel_id,
                    blob,
                    f"{file_entry.id}_p{part_number}.bin"
                )

                # Record chunk
                chunk_info = FileChunk(
                    message_id=msg.message_id,
                    size=len(blob),
                    part_number=part_number
                )
                file_entry.chunks.append(chunk_info)

                part_number += 1
                total_uploaded += len(chunk_data) # Original size

            # Update dataset
            async with self.dataset_lock:
                # RELOAD DATASET to prevent race conditions (lost updates)
                # This ensures we merge with any changes that happened while we were uploading chunks
                await self._load_dataset_internal()

                # Update total size
                file_entry.size = total_uploaded

                self.dataset.files.append(file_entry)
                await self._save_dataset_internal()

            return file_entry

        except Exception as e:
            logger.error(f"Upload failed: {e}")
            # Rollback?
            # We should probably delete uploaded chunks.
            # But for now, we just don't add the file to the dataset, so it's "lost" (orphaned in Telegram).
            # A cleanup job could find orphans.
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
        batch_size = 5
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i+batch_size]
            # Create tasks for the batch
            tasks = [self.tg.get_chunk_bytes(self.channel_id, c.message_id) for c in batch]
            # Wait for all in batch to complete
            results = await asyncio.gather(*tasks)

            for raw_data in results:
                iv = raw_data[:12]
                tag = raw_data[12:28]
                ciphertext = raw_data[28:]

                plaintext = decrypt_chunk(ciphertext, encryption_key, iv, tag)
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
