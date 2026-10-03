"""Encrypt one runner-bootstrap response to a fresh in-memory client key."""
import base64
import json
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def encode(value):
    return base64.b64encode(value).decode('ascii')


def decode(value):
    return base64.b64decode(value, validate=True)


def client_key():
    private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    public = private.public_key().public_bytes(serialization.Encoding.DER,
                                               serialization.PublicFormat.SubjectPublicKeyInfo)
    return private, encode(public)


def seal(public, request_id, payload):
    key = serialization.load_der_public_key(decode(public))
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 3072:
        raise ValueError('Expected an RSA public key of at least 3072 bits')
    secret = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    aad = request_id.encode('ascii')
    ciphertext = AESGCM(secret).encrypt(nonce, json.dumps(payload).encode(), aad)
    wrapped = key.encrypt(secret, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                              algorithm=hashes.SHA256(), label=aad))
    return {'version': 1, 'request_id': request_id, 'key': encode(wrapped),
            'nonce': encode(nonce), 'ciphertext': encode(ciphertext)}


def open_response(private, request_id, packet):
    if packet.get('version') != 1 or packet.get('request_id') != request_id:
        raise ValueError('Response belongs to a different request')
    aad = request_id.encode('ascii')
    secret = private.decrypt(decode(packet['key']), padding.OAEP(
        mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=aad))
    raw = AESGCM(secret).decrypt(decode(packet['nonce']), decode(packet['ciphertext']), aad)
    return json.loads(raw)
