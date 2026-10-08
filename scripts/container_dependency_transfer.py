#!/usr/bin/env python3
"""Encrypt private dependency transfer files before storing them in Actions caches."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
from pathlib import Path


MAGIC = b"VERJSON-DEPENDENCY-TRANSFER-1\n"
SALT_BYTES = 16
TAG_BYTES = hashlib.sha256().digest_size
BLOCK_BYTES = 16
CHUNK_BYTES = 1024 * 1024


def _key_from_environment() -> bytes:
    value = os.environ.get("TRANSFER_KEY", "")
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("TRANSFER_KEY must be 32 random bytes encoded as lowercase hex")
    return bytes.fromhex(value)


def _derive_keys(key: bytes, salt: bytes) -> tuple[bytes, bytes]:
    pseudorandom_key = hmac.new(salt, key, hashlib.sha256).digest()
    encryption_password = hmac.new(
        pseudorandom_key, b"verjson-dependency-transfer-encryption\x01", hashlib.sha256
    ).digest()
    authentication_key = hmac.new(
        pseudorandom_key, b"verjson-dependency-transfer-authentication\x01", hashlib.sha256
    ).digest()
    return encryption_password, authentication_key


def _openssl(source: Path, destination: Path, password: bytes, salt: bytes, *, decrypt: bool) -> None:
    read_fd, write_fd = os.pipe()
    operation = "-d" if decrypt else "-e"
    command = [
        "openssl",
        "enc",
        operation,
        "-aes-256-cbc",
        "-pbkdf2",
        "-iter",
        "10000",
        "-salt",
        "-S",
        salt.hex(),
        "-pass",
        f"fd:{read_fd}",
        "-in",
        str(source),
        "-out",
        str(destination),
    ]
    try:
        process = subprocess.Popen(command, pass_fds=(read_fd,), stderr=subprocess.PIPE)
        os.close(read_fd)
        read_fd = -1
        with os.fdopen(write_fd, "wb") as password_pipe:
            write_fd = -1
            password_pipe.write(password.hex().encode("ascii") + b"\n")
        _, stderr = process.communicate()
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise ValueError(f"OpenSSL transfer encryption failed: {detail or 'unknown error'}")
    finally:
        if read_fd >= 0:
            os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)


def _input_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"transfer source must be a regular non-symlink file: {path}")


def _destination_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise ValueError(f"transfer destination already exists: {path}")


def _temporary_file(parent: Path, prefix: str) -> Path:
    fd, name = tempfile.mkstemp(dir=parent, prefix=prefix)
    os.close(fd)
    return Path(name)


def _publish_no_replace(source: Path, destination: Path) -> None:
    os.link(source, destination, follow_symlinks=False)
    source.unlink()


def encrypt(source: Path, destination: Path, key: bytes) -> None:
    _input_file(source)
    _destination_file(destination)
    salt = os.urandom(SALT_BYTES)
    encryption_password, authentication_key = _derive_keys(key, salt)
    ciphertext = _temporary_file(destination.parent, ".dependency-ciphertext-")
    assembled = _temporary_file(destination.parent, ".dependency-encrypted-")
    try:
        _openssl(source, ciphertext, encryption_password, salt, decrypt=False)
        authenticator = hmac.new(authentication_key, MAGIC + salt, hashlib.sha256)
        with ciphertext.open("rb") as encrypted, assembled.open("wb") as output:
            output.write(MAGIC)
            output.write(salt)
            while chunk := encrypted.read(CHUNK_BYTES):
                authenticator.update(chunk)
                output.write(chunk)
            output.write(authenticator.digest())
        os.chmod(assembled, 0o600)
        _publish_no_replace(assembled, destination)
    finally:
        ciphertext.unlink(missing_ok=True)
        assembled.unlink(missing_ok=True)


def decrypt(source: Path, destination: Path, key: bytes) -> None:
    _input_file(source)
    _destination_file(destination)
    ciphertext = _temporary_file(destination.parent, ".dependency-ciphertext-")
    plaintext = _temporary_file(destination.parent, ".dependency-plaintext-")
    try:
        total_size = source.stat().st_size
        minimum_size = len(MAGIC) + SALT_BYTES + BLOCK_BYTES + TAG_BYTES
        if total_size < minimum_size:
            raise ValueError("encrypted dependency transfer is truncated")
        ciphertext_size = total_size - len(MAGIC) - SALT_BYTES - TAG_BYTES
        if ciphertext_size % BLOCK_BYTES:
            raise ValueError("encrypted dependency transfer has an invalid ciphertext length")
        with source.open("rb") as encrypted, ciphertext.open("wb") as body:
            magic = encrypted.read(len(MAGIC))
            salt = encrypted.read(SALT_BYTES)
            if magic != MAGIC or len(salt) != SALT_BYTES:
                raise ValueError("encrypted dependency transfer has an unknown format")
            encryption_password, authentication_key = _derive_keys(key, salt)
            authenticator = hmac.new(authentication_key, MAGIC + salt, hashlib.sha256)
            remaining = ciphertext_size
            while remaining:
                chunk = encrypted.read(min(CHUNK_BYTES, remaining))
                if not chunk:
                    raise ValueError("encrypted dependency transfer is truncated")
                authenticator.update(chunk)
                body.write(chunk)
                remaining -= len(chunk)
            supplied_tag = encrypted.read(TAG_BYTES)
            if len(supplied_tag) != TAG_BYTES or encrypted.read(1):
                raise ValueError("encrypted dependency transfer has an invalid authentication tag")
            if not hmac.compare_digest(authenticator.digest(), supplied_tag):
                raise ValueError("encrypted dependency transfer authentication failed")
        _openssl(ciphertext, plaintext, encryption_password, salt, decrypt=True)
        os.chmod(plaintext, 0o600)
        _publish_no_replace(plaintext, destination)
    finally:
        ciphertext.unlink(missing_ok=True)
        plaintext.unlink(missing_ok=True)


def restore_tar(source: Path, destination: Path, key: bytes) -> None:
    """Stream authenticated content into a new directory.

    This avoids archive-sized temporary copies.
    """
    if not hasattr(tarfile, "data_filter"):
        raise ValueError("streaming dependency restore requires Python 3.12 or newer")
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"restore destination already exists: {destination}")
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise ValueError(f"restore parent must be an existing directory: {destination.parent}")

    staging = Path(tempfile.mkdtemp(dir=destination.parent, prefix=".dependency-restore-"))
    published = False
    process: subprocess.Popen[bytes] | None = None
    feeder: threading.Thread | None = None
    feed_errors: list[Exception] = []
    try:
        source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(source_fd, "rb") as encrypted:
            if not stat.S_ISREG(os.fstat(encrypted.fileno()).st_mode):
                raise ValueError("transfer source must be a regular non-symlink file")
            total_size = os.fstat(encrypted.fileno()).st_size
            minimum_size = len(MAGIC) + SALT_BYTES + BLOCK_BYTES + TAG_BYTES
            if total_size < minimum_size:
                raise ValueError("encrypted dependency transfer is truncated")
            ciphertext_size = total_size - len(MAGIC) - SALT_BYTES - TAG_BYTES
            if ciphertext_size % BLOCK_BYTES:
                raise ValueError("encrypted dependency transfer has an invalid ciphertext length")
            magic = encrypted.read(len(MAGIC))
            salt = encrypted.read(SALT_BYTES)
            if magic != MAGIC or len(salt) != SALT_BYTES:
                raise ValueError("encrypted dependency transfer has an unknown format")
            encryption_password, authentication_key = _derive_keys(key, salt)
            authenticator = hmac.new(authentication_key, MAGIC + salt, hashlib.sha256)
            remaining = ciphertext_size
            while remaining:
                chunk = encrypted.read(min(CHUNK_BYTES, remaining))
                if not chunk:
                    raise ValueError("encrypted dependency transfer is truncated")
                authenticator.update(chunk)
                remaining -= len(chunk)
            supplied_tag = encrypted.read(TAG_BYTES)
            if len(supplied_tag) != TAG_BYTES or encrypted.read(1):
                raise ValueError("encrypted dependency transfer has an invalid authentication tag")
            if not hmac.compare_digest(authenticator.digest(), supplied_tag):
                raise ValueError("encrypted dependency transfer authentication failed")

            body_offset = len(MAGIC) + SALT_BYTES
            encrypted.seek(body_offset)
            read_fd, write_fd = os.pipe()
            try:
                command = [
                    "openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "10000",
                    "-salt", "-S", salt.hex(), "-pass", f"fd:{read_fd}",
                ]
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    pass_fds=(read_fd,),
                )
            except Exception:
                os.close(write_fd)
                raise
            finally:
                os.close(read_fd)
            with os.fdopen(write_fd, "wb") as password_pipe:
                password_pipe.write(encryption_password.hex().encode("ascii"))

            def feed_ciphertext() -> None:
                stream_authenticator = hmac.new(authentication_key, MAGIC + salt, hashlib.sha256)
                remaining_body = ciphertext_size
                try:
                    assert process is not None and process.stdin is not None
                    while remaining_body:
                        chunk = encrypted.read(min(CHUNK_BYTES, remaining_body))
                        if not chunk:
                            raise ValueError("encrypted dependency transfer changed during restore")
                        stream_authenticator.update(chunk)
                        process.stdin.write(chunk)
                        remaining_body -= len(chunk)
                    final_tag = encrypted.read(TAG_BYTES)
                    if len(final_tag) != TAG_BYTES or encrypted.read(1):
                        raise ValueError("encrypted dependency transfer changed during restore")
                    if not hmac.compare_digest(stream_authenticator.digest(), final_tag):
                        raise ValueError("encrypted dependency transfer changed during restore")
                except Exception as error:
                    feed_errors.append(error)
                finally:
                    if process is not None and process.stdin is not None:
                        try:
                            process.stdin.close()
                        except OSError:
                            pass

            feeder = threading.Thread(target=feed_ciphertext, daemon=True)
            feeder.start()
            extraction_error: Exception | None = None
            try:
                assert process.stdout is not None
                with tarfile.open(fileobj=process.stdout, mode="r|gz") as archive:
                    archive.extractall(path=staging, filter="data")
                while chunk := process.stdout.read(CHUNK_BYTES):
                    if chunk.strip(b"\0"):
                        raise ValueError("dependency archive contains trailing data")
            except Exception as error:
                extraction_error = error
            finally:
                if extraction_error is not None and process.poll() is None:
                    process.terminate()
                if process.stdout is not None:
                    process.stdout.close()
                feeder.join()
                return_code = process.wait()
                stderr = process.stderr.read() if process.stderr is not None else b""

            if extraction_error is not None:
                raise ValueError(f"dependency archive extraction failed: {extraction_error}") from extraction_error
            if feed_errors:
                raise ValueError(f"dependency transfer failed while streaming: {feed_errors[0]}") from feed_errors[0]
            if return_code != 0:
                detail = stderr.decode("utf-8", errors="replace").strip()
                raise ValueError(f"OpenSSL decryption failed: {detail or 'unknown error'}")
            if destination.exists() or destination.is_symlink():
                raise ValueError(f"restore destination appeared during extraction: {destination}")
            os.rename(staging, destination)
            published = True
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait()
        if not published:
            shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("encrypt", "decrypt", "restore-tar"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        key = _key_from_environment()
        operation = {
            "encrypt": encrypt,
            "decrypt": decrypt,
            "restore-tar": restore_tar,
        }[arguments.operation]
        operation(arguments.source, arguments.destination, key)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        parser.exit(1, f"dependency transfer failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
