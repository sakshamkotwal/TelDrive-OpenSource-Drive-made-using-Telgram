import pytest
from unittest.mock import MagicMock, AsyncMock
from teledrive.backend.drive import DriveManager
from teledrive.backend.models import DriveDataset
from teledrive.backend.telegram_bot import TelegramClient

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
async def test_dataset_editing(drive, mock_tg):
    # Setup - Pretend we have a message ID
    drive.dataset_message_id = 999

    # Mock edit_dataset_file to return a message object
    msg_mock = AsyncMock()
    msg_mock.message_id = 1000 # New ID
    mock_tg.edit_dataset_file.return_value = msg_mock

    # Save (which triggers internal save)
    await drive.save_dataset()

    # Verify edit was called
    assert mock_tg.edit_dataset_file.called
    assert drive.dataset_message_id == 1000

@pytest.mark.asyncio
async def test_dataset_fallback_upload(drive, mock_tg):
    # Setup - No message ID
    drive.dataset_message_id = None

    # Mock upload_dataset
    msg_mock = AsyncMock()
    msg_mock.message_id = 555
    mock_tg.upload_dataset.return_value = msg_mock

    # Save
    await drive.save_dataset()

    # Verify upload was called
    assert mock_tg.upload_dataset.called
    assert drive.dataset_message_id == 555
