import pytest
from unittest.mock import MagicMock, AsyncMock
import asyncio
from teledrive.backend.drive import DriveManager
from teledrive.backend.models import DriveDataset
from teledrive.backend.telegram_bot import TelegramClient
from teledrive.backend.config import settings

@pytest.fixture
def mock_tg():
    tg = AsyncMock(spec=TelegramClient)
    # Mock upload_chunk to return a message object with message_id
    msg_mock = AsyncMock()
    msg_mock.message_id = 123
    tg.upload_chunk.return_value = msg_mock

    # Mock get_pinned_message to return None (fresh drive) or object with document
    tg.get_pinned_message.return_value = None

    return tg

@pytest.fixture
def drive(mock_tg):
    dm = DriveManager(mock_tg, channel_id=100)
    # Mock dataset init manually to skip `ensure_initialized` call to `load_dataset` which mocks
    dm.dataset = DriveDataset()
    return dm

@pytest.mark.asyncio
async def test_upload_download_cycle(drive, mock_tg):
    # Setup
    key = b"0"*32 # 32 bytes for AES-256
    content = b"Hello World" * 100

    # Mock file stream
    file_stream = AsyncMock()
    # read needs to return content then empty bytes
    file_stream.read.side_effect = [content, b""]

    # Upload
    uploaded_file = await drive.upload_file(file_stream, "test.txt", 0, None, key)

    assert uploaded_file.name == "test.txt"
    assert uploaded_file.size == len(content)
    assert len(uploaded_file.chunks) == 1
    assert mock_tg.upload_chunk.called

    # Capture what was uploaded
    call_args = mock_tg.upload_chunk.call_args
    # args: (channel_id, data, filename)
    uploaded_blob = call_args[0][1]

    # Size check: Content + IV(12) + Tag(16)
    assert len(uploaded_blob) == len(content) + 28

    # Mock Download
    mock_tg.get_chunk_bytes.return_value = uploaded_blob

    # Download
    chunks = []
    async for chunk in drive.download_file(uploaded_file.id, key):
        chunks.append(chunk)

    reassembled = b"".join(chunks)
    assert reassembled == content

@pytest.mark.asyncio
async def test_chunking(drive, mock_tg):
    # Temporarily reduce chunk size for testing
    original_chunk_size = settings.MAX_CHUNK_SIZE
    settings.MAX_CHUNK_SIZE = 10 # very small chunk

    try:
        key = b"0"*32
        content = b"1234567890" * 3 # 30 bytes

        # Mock file stream to yield 30 bytes.
        # But `upload_file` calls `read(chunk_size)`.
        # We need to simulate stream behavior.

        # Implementation of read logic for mock
        stream_pos = 0
        async def mock_read(n):
            nonlocal stream_pos
            if stream_pos >= len(content):
                return b""
            chunk = content[stream_pos:stream_pos+n]
            stream_pos += len(chunk)
            return chunk

        file_stream = AsyncMock()
        file_stream.read.side_effect = mock_read

        uploaded_file = await drive.upload_file(file_stream, "test.txt", 0, None, key)

        # Should have 3 chunks (10 bytes each)
        assert len(uploaded_file.chunks) == 3
        assert uploaded_file.size == 30

    finally:
        settings.MAX_CHUNK_SIZE = original_chunk_size

@pytest.mark.asyncio
async def test_create_folder(drive, mock_tg):
    folder = await drive.create_folder("New Folder")
    assert folder.name == "New Folder"
    assert len(drive.dataset.folders) == 1
    assert mock_tg.upload_dataset.called # Should sync
