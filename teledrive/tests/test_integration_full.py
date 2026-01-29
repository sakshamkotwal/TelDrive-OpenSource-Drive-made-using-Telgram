import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch
import json
import io

# We need to mock the telegram client BEFORE importing main if it initializes global state
# But main.py initializes in lifespan.

from teledrive.backend import main
from teledrive.backend.models import DriveDataset

# Mock Telegram Client
class MockTelegramClient:
    def __init__(self, token):
        self.files = {}
        self.pinned_message = None
        self.messages = {}
        self.msg_counter = 100

    async def get_me(self):
        return MagicMock(username="mock_bot")

    async def get_chat(self, chat_id):
        m = MagicMock()
        m.pinned_message = self.pinned_message
        return m

    async def get_pinned_message(self, chat_id):
        return self.pinned_message

    async def upload_dataset(self, chat_id, data):
        msg_id = self.msg_counter
        self.msg_counter += 1

        # Store file content
        file_id = f"file_{msg_id}"
        self.files[file_id] = data

        # Create message object
        msg = MagicMock()
        msg.message_id = msg_id
        msg.document.file_id = file_id

        # Pin it
        self.pinned_message = msg
        return msg

    async def edit_dataset_file(self, chat_id, message_id, data):
        # Update file content
        file_id = f"file_{message_id}"
        self.files[file_id] = data

        msg = MagicMock()
        msg.message_id = message_id
        msg.document.file_id = file_id
        self.pinned_message = msg
        return msg

    async def upload_chunk(self, chat_id, data, filename):
        msg_id = self.msg_counter
        self.msg_counter += 1

        file_id = f"file_{msg_id}"
        self.files[file_id] = data

        msg = MagicMock()
        msg.message_id = msg_id
        msg.document.file_id = file_id
        self.messages[msg_id] = msg
        return msg

    async def get_chunk_bytes(self, chat_id, message_id):
        if message_id not in self.messages:
             raise Exception(f"Message {message_id} not found")
        msg = self.messages[message_id]
        file_id = msg.document.file_id
        return self.files[file_id]

    async def download_file(self, file_id):
        return self.files.get(file_id, b"")

    async def delete_message(self, chat_id, message_id):
        if message_id in self.messages:
            del self.messages[message_id]

@pytest.fixture
def client():
    # Patch the global telegram_client in main
    main.telegram_client = MockTelegramClient("mock_token")

    # Reset drive managers
    from teledrive.backend.drive import _drive_managers
    _drive_managers.clear()

    return TestClient(main.app)

def test_full_cycle(client):
    channel_id = "-100123456789"
    password = "password123"
    # Derive key (client side usually) -> hex
    # Key derivation (simple for test)
    import hashlib
    key = hashlib.sha256(password.encode()).hexdigest()

    headers = {
        "X-Channel-ID": channel_id,
        "X-Encryption-Key": key
    }

    # 1. Verify Setup
    res = client.post(f"/api/setup/verify?channel_id={channel_id}")
    assert res.status_code == 200

    # 2. Upload File
    file_content = b"Hello World This is a test file."
    files = {'file': ('test.txt', file_content, 'text/plain')}

    res = client.post("/api/files/upload", headers=headers, files=files)
    assert res.status_code == 200
    uploaded_file = res.json()
    file_id = uploaded_file['id']
    assert uploaded_file['name'] == 'test.txt'

    # 3. List Files
    res = client.get("/api/files", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert len(data['files']) == 1
    assert data['files'][0]['id'] == file_id
    assert data['files'][0]['size'] > 0

    # 4. Download File
    res = client.get(f"/api/files/download/{file_id}", headers=headers)
    assert res.status_code == 200
    assert res.content == file_content

    # 5. Create Folder
    res = client.post("/api/folders", headers=headers, data={"name": "MyFolder"})
    assert res.status_code == 200
    folder_id = res.json()['id']

    # 6. Upload to Folder
    res = client.post("/api/files/upload", headers=headers, files={'file': ('nested.txt', b"Nested", 'text/plain')}, data={"parent_id": folder_id})
    assert res.status_code == 200

    # 7. List Folder
    res = client.get(f"/api/files?folder_id={folder_id}", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert len(data['files']) == 1
    assert data['files'][0]['name'] == 'nested.txt'

    # 8. Search
    res = client.get("/api/files?q=nested", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert len(data['files']) == 1
    assert data['files'][0]['name'] == 'nested.txt'

    # 9. Delete File
    res = client.delete(f"/api/items/{file_id}", headers=headers)
    assert res.status_code == 200

    # 10. Verify Deletion
    res = client.get("/api/files", headers=headers)
    data = res.json()
    assert len(data['files']) == 0 # Only folder remains (root view)

    print("\nIntegration Test Passed!")

if __name__ == "__main__":
    # If run directly
    pytest.main([__file__])
