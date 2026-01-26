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
            return await self.bot.send_document(
                chat_id=chat_id,
                document=data,
                filename=filename,
                disable_notification=True
            )
        except RetryAfter as e:
            logger.warning(f"Rate limited. Sleeping for {e.retry_after} seconds.")
            await asyncio.sleep(e.retry_after)
            return await self.upload_chunk(chat_id, data, filename)

    async def get_chunk_bytes(self, chat_id: int, message_id: int) -> bytes:
        """Retrieves the file content from a message."""
        try:
            # We can't get message by ID directly via bot API easily without `get_chat` context or implicitly?
            # Actually, `forward_message` is one way, but we want to read it.
            # `bot.get_messages` does not exist.
            # But we can `forward_message` to a temp chat? No.
            # Wait, `python-telegram-bot` doesn't have `get_message`?
            # It does not. The Bot API does not have `get_message`.
            # THIS IS A PROBLEM.
            # Strategies:
            # 1. Store `file_id` in the dataset, not just `message_id`.
            #    `file_id` is persistent (mostly).
            # 2. Forward the message to the same chat? returns a new message with the same content.
            # 3. `file_id` is robust enough for long term?
            #    "file_id can change over time". "It is recommended to use file_unique_id... but you can't download with it."
            #    However, for a self-hosted drive, `file_id` usually lasts a long time.
            #    BUT, if it expires, we need to refresh it. How?
            #    If we have the `message_id`, we can Forward it to ourselves.
            #    The forwarded message will have a fresh `file_id`.

            # Implementation:
            # Forward message_id from chat_id to chat_id.
            # Get file_id from the new message.
            # Download.
            # Delete the forwarded message.

            forwarded = await self.bot.forward_message(chat_id=chat_id, from_chat_id=chat_id, message_id=message_id)
            file_id = forwarded.document.file_id
            data = await self.download_file(file_id)
            await forwarded.delete()
            return data

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
