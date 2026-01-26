import os
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
import logging

logger = logging.getLogger(__name__)

def encrypt_chunk(data: bytes, key: bytes) -> tuple[bytes, bytes, bytes]:
    """
    Encrypts data using AES-GCM.
    Returns (ciphertext, iv, tag).
    """
    iv = os.urandom(12)
    encryptor = Cipher(
        algorithms.AES(key),
        modes.GCM(iv),
        backend=default_backend()
    ).encryptor()
    ciphertext = encryptor.update(data) + encryptor.finalize()
    return ciphertext, iv, encryptor.tag

def decrypt_chunk(data: bytes, key: bytes, iv: bytes, tag: bytes) -> bytes:
    """
    Decrypts data using AES-GCM.
    """
    decryptor = Cipher(
        algorithms.AES(key),
        modes.GCM(iv, tag),
        backend=default_backend()
    ).decryptor()
    return decryptor.update(data) + decryptor.finalize()
