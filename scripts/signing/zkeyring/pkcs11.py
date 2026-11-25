# SPDX-License-Identifier: Apache-2.0

import re
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ec, utils

from zkeyring import ZkeyringError
from zkeyring.backends import load_public_key

try:
    import pkcs11
    from pkcs11.util.ec import encode_ec_public_key
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

    def public_key(self) -> ec.EllipticCurvePublicKey:
        try:
            with self._token.open(user_pin=self._pin) as session:
                key = session.get_key(
                    object_class=pkcs11.ObjectClass.PUBLIC_KEY,
                    key_type=pkcs11.KeyType.EC,
                    id=self._id,
                )
                der = encode_ec_public_key(key)
        except (pkcs11.PKCS11Error, ValueError) as exc:
            raise ZkeyringError('could not read the PKCS#11 public key') from exc
        return load_public_key(der)

    def sign_digest(self, digest: bytes) -> bytes:
        try:
            with self._token.open(user_pin=self._pin) as session:
                key = session.get_key(
                    object_class=pkcs11.ObjectClass.PRIVATE_KEY,
                    key_type=pkcs11.KeyType.EC,
                    id=self._id,
                )
                if not key[pkcs11.Attribute.SENSITIVE] or key[pkcs11.Attribute.EXTRACTABLE]:
                    raise ZkeyringError('PKCS#11 signing requires a sensitive non-extractable key')
                signature = key.sign(digest, mechanism=pkcs11.Mechanism.ECDSA)
        except pkcs11.PKCS11Error as exc:
            raise ZkeyringError('PKCS#11 could not sign the digest') from exc
        if len(signature) != 64:
            raise ZkeyringError('expected a raw P-256 signature from PKCS#11')
        r = int.from_bytes(signature[:32], 'big')
        s = int.from_bytes(signature[32:], 'big')
        return utils.encode_dss_signature(r, s)
