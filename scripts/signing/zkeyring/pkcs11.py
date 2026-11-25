# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Romain Pelletant

import re
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ed25519, utils

from zkeyring import ZkeyringError
from zkeyring.backends import PublicKey, load_public_key

try:
    import pkcs11
    from pkcs11.util.ec import encode_ec_public_key
    from pkcs11.util.rsa import encode_rsa_public_key
except ImportError as exc:
    raise ZkeyringError('install python-pkcs11 to use this provider') from exc


class PKCS11Provider:
    def __init__(self, module: Path, token: str, key_id: str, pin: str):
        if not re.fullmatch(r'(?:[0-9a-fA-F]{2})+', key_id):
            raise ZkeyringError('PKCS#11 key must be a hexadecimal CKA_ID')
        if not module.is_file():
            raise ZkeyringError('PKCS#11 module file does not exist')
        try:
            self._token = pkcs11.lib(str(module)).get_token(token_label=token)
        except (pkcs11.PKCS11Error, OSError) as exc:
            raise ZkeyringError('could not open the PKCS#11 token') from exc
        self._id = bytes.fromhex(key_id)
        self._pin = pin

    def public_key(self) -> PublicKey:
        try:
            with self._token.open(user_pin=self._pin) as session:
                key = session.get_key(
                    object_class=pkcs11.ObjectClass.PUBLIC_KEY,
                    id=self._id,
                )
                if key.key_type == pkcs11.KeyType.RSA:
                    return load_public_key(encode_rsa_public_key(key))
                if key.key_type == pkcs11.KeyType.EC:
                    return load_public_key(encode_ec_public_key(key))
                if key.key_type == pkcs11.KeyType.EC_EDWARDS:
                    point = key[pkcs11.Attribute.EC_POINT]
                    if len(point) == 34 and point.startswith(b'\x04\x20'):
                        point = point[2:]
                    return ed25519.Ed25519PublicKey.from_public_bytes(point)
        except (pkcs11.PKCS11Error, ValueError) as exc:
            raise ZkeyringError('could not read the PKCS#11 public key') from exc
        raise ZkeyringError('unsupported PKCS#11 public key type')

    def sign_digest(self, digest: bytes) -> bytes:
        try:
            with self._token.open(user_pin=self._pin) as session:
                key = session.get_key(
                    object_class=pkcs11.ObjectClass.PRIVATE_KEY,
                    id=self._id,
                )
                if not key[pkcs11.Attribute.SENSITIVE] or key[pkcs11.Attribute.EXTRACTABLE]:
                    raise ZkeyringError('PKCS#11 signing requires a sensitive non-extractable key')
                if key.key_type == pkcs11.KeyType.RSA:
                    return key.sign(
                        digest,
                        mechanism=pkcs11.Mechanism.RSA_PKCS_PSS,
                        mechanism_param=(pkcs11.Mechanism.SHA256, pkcs11.MGF.SHA256, 32),
                    )
                if key.key_type == pkcs11.KeyType.EC_EDWARDS:
                    return key.sign(digest, mechanism=pkcs11.Mechanism.EDDSA)
                if key.key_type != pkcs11.KeyType.EC:
                    raise ZkeyringError('unsupported PKCS#11 signing key type')
                signature = key.sign(digest, mechanism=pkcs11.Mechanism.ECDSA)
        except pkcs11.PKCS11Error as exc:
            raise ZkeyringError('PKCS#11 could not sign the digest') from exc
        if len(signature) != 64:
            raise ZkeyringError('expected a raw P-256 signature from PKCS#11')
        r = int.from_bytes(signature[:32], 'big')
        s = int.from_bytes(signature[32:], 'big')
        return utils.encode_dss_signature(r, s)
