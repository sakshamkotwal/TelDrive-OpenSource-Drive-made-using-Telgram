from pydantic import BaseModel, Field
from typing import List, Optional
from uuid import uuid4
import time

def generate_uuid():
    return str(uuid4())

class FileChunk(BaseModel):
    message_id: int
    size: int
    part_number: int

class TeledriveItem(BaseModel):
    id: str = Field(default_factory=generate_uuid)
    name: str
    created_at: float = Field(default_factory=time.time)
    parent_id: Optional[str] = None  # None indicates root

class TeledriveFile(TeledriveItem):
    size: int
    mime_type: str = "application/octet-stream"
    chunks: List[FileChunk] = []
    is_encrypted: bool = True
    # Security: In a real system, we might store encryption parameters here
    # For this implementation, we assume key is derived from password and IV/Salt might be stored here or prepended to data
    # Let's store salt/iv here to be explicit
    encryption_iv: Optional[str] = None
    encryption_tag: Optional[str] = None # For GCM authentication tag if needed

class TeledriveFolder(TeledriveItem):
    pass

class DriveDataset(BaseModel):
    version: int = 1
    root_folder_id: Optional[str] = None # Typically None is root, but we can have a conceptual root
    files: List[TeledriveFile] = []
    folders: List[TeledriveFolder] = []
    last_updated: float = Field(default_factory=time.time)

class UserSession(BaseModel):
    channel_id: int
    password_hash: Optional[str] = None # To verify password locally?
    # We might not need to store this in the dataset, this is ephemeral.
