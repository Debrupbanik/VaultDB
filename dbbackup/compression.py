"""
Compression utilities for DBBackup.

Supports gzip, bzip2, and lzma compression with configurable levels.
Handles both compression and decompression of backup files.
"""

import os
import gzip
import bz2
import lzma
import shutil
from pathlib import Path
from typing import Optional


COMPRESSION_EXTENSIONS = {
    "gzip": ".gz",
    "bzip2": ".bz2",
    "lzma": ".xz",
    "none": "",
}


def get_compression_extension(method: str) -> str:
    """Get the file extension for a compression method."""
    return COMPRESSION_EXTENSIONS.get(method.lower(), "")


def compress_file(
    input_path: str,
    method: str = "gzip",
    level: int = 6,
    remove_original: bool = True,
) -> tuple[str, int, int]:
    """
    Compress a file using the specified method.

    Args:
        input_path: Path to the file to compress
        method: Compression method (gzip, bzip2, lzma, none)
        level: Compression level (1-9)
        remove_original: Whether to remove the original file after compression

    Returns:
        Tuple of (output_path, original_size, compressed_size)
    """
    method = method.lower()

    if method == "none":
        size = os.path.getsize(input_path)
        return input_path, size, size

    ext = COMPRESSION_EXTENSIONS.get(method)
    if ext is None:
        raise ValueError(f"Unsupported compression method: {method}")

    # Check if the file already has this compression extension
    if input_path.endswith(ext):
        output_path = input_path
    else:
        output_path = input_path + ext
    original_size = os.path.getsize(input_path)

    # Clamp compression level
    level = max(1, min(9, level))

    # Chunk size for reading/writing (64KB)
    chunk_size = 65536

    if method == "gzip":
        with open(input_path, "rb") as f_in:
            with gzip.open(output_path, "wb", compresslevel=level) as f_out:
                while True:
                    chunk = f_in.read(chunk_size)
                    if not chunk:
                        break
                    f_out.write(chunk)

    elif method == "bzip2":
        with open(input_path, "rb") as f_in:
            with bz2.open(output_path, "wb", compresslevel=level) as f_out:
                while True:
                    chunk = f_in.read(chunk_size)
                    if not chunk:
                        break
                    f_out.write(chunk)

    elif method == "lzma":
        # LZMA preset maps to compression level (0-9)
        preset = min(level, 9)
        with open(input_path, "rb") as f_in:
            with lzma.open(output_path, "wb", preset=preset) as f_out:
                while True:
                    chunk = f_in.read(chunk_size)
                    if not chunk:
                        break
                    f_out.write(chunk)

    compressed_size = os.path.getsize(output_path)

    if remove_original:
        os.remove(input_path)

    return output_path, original_size, compressed_size


def decompress_file(
    input_path: str,
    output_path: Optional[str] = None,
    remove_compressed: bool = False,
) -> str:
    """
    Decompress a backup file.

    Auto-detects compression method from file extension.

    Args:
        input_path: Path to the compressed file
        output_path: Path for decompressed output (auto-generated if not specified)
        remove_compressed: Whether to remove the compressed file after decompression

    Returns:
        Path to the decompressed file
    """
    input_path_obj = Path(input_path)

    # Detect compression method from extension
    ext = input_path_obj.suffix.lower()

    method_map = {
        ".gz": "gzip",
        ".bz2": "bzip2",
        ".xz": "lzma",
    }

    method = method_map.get(ext)
    if method is None:
        # Not compressed, return as-is
        return input_path

    # Determine output path
    if output_path is None:
        output_path = str(input_path_obj.with_suffix(""))

    chunk_size = 65536

    if method == "gzip":
        with gzip.open(input_path, "rb") as f_in:
            with open(output_path, "wb") as f_out:
                while True:
                    chunk = f_in.read(chunk_size)
                    if not chunk:
                        break
                    f_out.write(chunk)

    elif method == "bzip2":
        with bz2.open(input_path, "rb") as f_in:
            with open(output_path, "wb") as f_out:
                while True:
                    chunk = f_in.read(chunk_size)
                    if not chunk:
                        break
                    f_out.write(chunk)

    elif method == "lzma":
        with lzma.open(input_path, "rb") as f_in:
            with open(output_path, "wb") as f_out:
                while True:
                    chunk = f_in.read(chunk_size)
                    if not chunk:
                        break
                    f_out.write(chunk)

    if remove_compressed:
        os.remove(input_path)

    return output_path


def get_compression_ratio(original_size: int, compressed_size: int) -> float:
    """Calculate compression ratio as a percentage."""
    if original_size == 0:
        return 0.0
    return (1 - compressed_size / original_size) * 100
