import telegram
from telegram.error import TelegramError, RetryAfter
import asyncio
import io
from typing import Optional, List, Union
import logging

logger = logging.getLogger(__name__)

class TelegramClient:
    def __init__(self, token: str):
        self.bot = telegram.Bot(token=token)

    async def get_me(self):
        return await self.bot.get_me()

    async def get_chat(self, chat_id: Union[int, str]):
        return await self.bot.get_chat(chat_id)

    async def get_pinned_message(self, chat_id: int) -> Optional[telegram.Message]:
        try:
            chat = await self.bot.get_chat(chat_id)
            return chat.pinned_message
        except TelegramError as e:
            logger.error(f"Error fetching pinned message: {e}")
            return None

    async def upload_dataset(self, chat_id: int, data: bytes) -> telegram.Message:
        """Uploads the dataset as a file and pins it."""
        try:
            # Upload new dataset file
            message = await self.bot.send_document(
                chat_id=chat_id,
                document=data,
                filename="teledrive_dataset.json",
                caption="Teledrive Dataset Source of Truth"
            )
            # Pin the new message
            await message.pin(disable_notification=True)
            return message
        except RetryAfter as e:
            logger.warning(f"Rate limited. Sleeping for {e.retry_after} seconds.")
            await asyncio.sleep(e.retry_after)
            return await self.upload_dataset(chat_id, data)
        except TelegramError as e:
            logger.error(f"Error uploading dataset: {e}")
            raise e

    async def edit_dataset_file(self, chat_id: int, message_id: int, data: bytes) -> telegram.Message:
        """Edits the existing pinned message to replace the dataset file."""
        try:
            # In python-telegram-bot v20+, edit_message_media returns the edited Message.
            # We use InputMediaDocument.
            from telegram import InputMediaDocument
            return await self.bot.edit_message_media(
                chat_id=chat_id,
                message_id=message_id,
                media=InputMediaDocument(data, filename="teledrive_dataset.json", caption="Teledrive Dataset Source of Truth")
            )
        except RetryAfter as e:
            logger.warning(f"Rate limited. Sleeping for {e.retry_after} seconds.")
            await asyncio.sleep(e.retry_after)
            return await self.edit_dataset_file(chat_id, message_id, data)
        except TelegramError as e:
            # If edit fails (e.g., message too old or deleted), we might need to fallback.
            # Caller should handle this.
            logger.error(f"Error editing dataset: {e}")
            raise e

    async def download_file(self, file_id: str) -> bytes:
        """Downloads a file from Telegram (used for dataset or chunks)."""
        try:
            new_file = await self.bot.get_file(file_id)
            # download_as_bytearray is deprecated/removed in v20+, use byte_array = await new_file.download_as_bytearray()
            # actually it's complicated in v20. It might store to memory or disk.
            # download_to_memory is the way.
            out = io.BytesIO()
            await new_file.download_to_memory(out)
            out.seek(0)
            return out.read()
        except TelegramError as e:
            logger.error(f"Error downloading file {file_id}: {e}")
            raise e

    async def upload_chunk(self, chat_id: int, data: bytes, filename: str) -> telegram.Message:
        try:
            logger.debug(f"Uploading chunk {filename} ({len(data)} bytes) to {chat_id}")
            msg = await self.bot.send_document(
                chat_id=chat_id,
                document=data,
                filename=filename,
                disable_notification=True
            )
            logger.debug(f"Chunk uploaded: message_id={msg.message_id}")
            return msg
        except RetryAfter as e:
            logger.warning(f"Rate limited uploading chunk. Sleeping for {e.retry_after} seconds.")
            await asyncio.sleep(e.retry_after)
            return await self.upload_chunk(chat_id, data, filename)
        except Exception as e:
            logger.error(f"Failed to upload chunk {filename}: {e}")
            raise e

    async def get_chunk_bytes(self, chat_id: int, message_id: int) -> bytes:
        """Retrieves the file content from a message."""
        try:
            logger.debug(f"Fetching chunk from message {message_id} in {chat_id}")
            # Forward message to self to get fresh file_id and valid download path
            forwarded = await self.bot.forward_message(chat_id=chat_id, from_chat_id=chat_id, message_id=message_id)

            if not forwarded.document:
                logger.error(f"Forwarded message {forwarded.message_id} has no document.")
                await forwarded.delete()
                raise TelegramError("Message has no document")

            file_id = forwarded.document.file_id
            data = await self.download_file(file_id)

            # Cleanup
            await forwarded.delete()
            logger.debug(f"Fetched chunk {message_id} successfully ({len(data)} bytes)")
            return data

        except RetryAfter as e:
             logger.warning(f"Rate limited fetching chunk. Sleeping for {e.retry_after} seconds.")
             await asyncio.sleep(e.retry_after)
             return await self.get_chunk_bytes(chat_id, message_id)
        except TelegramError as e:
            logger.error(f"Error fetching chunk {message_id}: {e}")
            raise e

    async def delete_message(self, chat_id: int, message_id: int):
        try:
            await self.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramError as e:
            logger.warning(f"Failed to delete message {message_id}: {e}")

    async def cleanup_old_pins(self, chat_id: int, keep_message_id: int):
        """Unpins and deletes old dataset messages."""
        # This is tricky because getting ALL pinned messages might not be possible easily if not simple.
        # But usually there is only one pinned message if we manage it correctly.
        # Actually get_chat returns `pinned_message` (singular) which is the most recent.
        # We might need to keep track of old ones to delete them?
        # For now, we assume we unpin/delete previous before pinning new?
        # Or we rely on the logic that we replace the pin.
        pass

    async def unpin_all(self, chat_id: int):
        try:
            await self.bot.unpin_all_chat_messages(chat_id)
        except Exception as e:
            logger.warning(f"Failed to unpin all: {e}")
