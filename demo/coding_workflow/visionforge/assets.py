from __future__ import annotations

import os
import struct
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath

from ..artifacts import Artifact, ArtifactStore


class ImageAssetError(ValueError):
    pass


@dataclass(frozen=True)
class ImageArtifactRef:
    asset_id: str
    uri: str
    relative_path: str
    mime_type: str
    sha256: str
    size_bytes: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if len(self.sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.sha256
        ):
            raise ImageAssetError("图片 SHA-256 无效")
        if self.asset_id != self.sha256 or self.uri != f"asset://{self.sha256}":
            raise ImageAssetError("图片 asset_id、uri 和 SHA-256 不一致")
        path = PurePosixPath(self.relative_path)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ImageAssetError("图片相对路径不安全")
        if self.mime_type not in {"image/png", "image/jpeg"}:
            raise ImageAssetError("图片只支持 PNG 或 JPEG")
        if self.size_bytes <= 0 or self.width <= 0 or self.height <= 0:
            raise ImageAssetError("图片大小和尺寸必须大于 0")

    def to_dict(self) -> dict[str, object]:
        return {
            "asset_id": self.asset_id,
            "uri": self.uri,
            "relative_path": self.relative_path,
            "mime_type": self.mime_type,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ImageArtifactRef":
        if not isinstance(value, dict):
            raise ImageAssetError("图片 Artifact 引用必须是对象")
        try:
            return cls(
                str(value["asset_id"]),
                str(value["uri"]),
                str(value["relative_path"]),
                str(value["mime_type"]),
                str(value["sha256"]),
                int(value["size_bytes"]),
                int(value["width"]),
                int(value["height"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ImageAssetError):
                raise
            raise ImageAssetError(f"图片 Artifact 引用字段无效: {exc}") from exc


class ImageAssetStore:
    """内容寻址图片存储；Artifact 和 SQLite 只保存引用元数据。"""

    PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
    JPEG_SOF_MARKERS = frozenset({
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    })

    def __init__(
        self,
        root: Path,
        *,
        max_size_bytes: int = 10 * 1024 * 1024,
        max_pixels: int = 24_000_000,
    ) -> None:
        if max_size_bytes <= 0 or max_pixels <= 0:
            raise ValueError("图片大小限制必须大于 0")
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_size_bytes = max_size_bytes
        self.max_pixels = max_pixels

    def put(self, data: bytes) -> ImageArtifactRef:
        if not isinstance(data, bytes) or not data:
            raise ImageAssetError("图片内容不能为空")
        if len(data) > self.max_size_bytes:
            raise ImageAssetError(
                f"图片超过大小限制: {len(data)} > {self.max_size_bytes}"
            )
        mime_type, extension, width, height = self._inspect(data)
        if width * height > self.max_pixels:
            raise ImageAssetError(
                f"图片像素超过限制: {width}x{height} > {self.max_pixels}"
            )
        digest = sha256(data).hexdigest()
        relative_path = f"{digest[:2]}/{digest}.{extension}"
        target = self._resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if sha256(target.read_bytes()).hexdigest() != digest:
                raise ImageAssetError("内容寻址图片发生哈希冲突")
        else:
            temporary_name = ""
            try:
                with tempfile.NamedTemporaryFile(
                    prefix=".image-", dir=target.parent, delete=False
                ) as stream:
                    stream.write(data)
                    temporary_name = stream.name
                os.replace(temporary_name, target)
            finally:
                if temporary_name:
                    Path(temporary_name).unlink(missing_ok=True)
        return ImageArtifactRef(
            digest,
            f"asset://{digest}",
            relative_path,
            mime_type,
            digest,
            len(data),
            width,
            height,
        )

    def create_artifact(
        self,
        artifacts: ArtifactStore,
        *,
        name: str,
        task_id: str,
        data: bytes,
        kind: str = "reference_image",
    ) -> tuple[str, ImageArtifactRef]:
        image = self.put(data)
        reference = artifacts.put(Artifact.create(
            name,
            task_id,
            image.to_dict(),
            kind=kind,
            metadata={
                "asset_uri": image.uri,
                "mime_type": image.mime_type,
                "sha256": image.sha256,
            },
        ))
        return reference, image

    def read(self, image: ImageArtifactRef) -> bytes:
        target = self._resolve(image.relative_path)
        if not target.is_file():
            raise ImageAssetError(f"图片资产不存在: {image.uri}")
        data = target.read_bytes()
        if sha256(data).hexdigest() != image.sha256:
            raise ImageAssetError(f"图片资产哈希校验失败: {image.uri}")
        return data

    def _resolve(self, relative_path: str) -> Path:
        path = PurePosixPath(relative_path)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ImageAssetError("图片资产路径不安全")
        resolved = (self.root / Path(*path.parts)).resolve()
        if not resolved.is_relative_to(self.root):
            raise ImageAssetError("图片资产路径越过存储边界")
        return resolved

    @classmethod
    def _inspect(cls, data: bytes) -> tuple[str, str, int, int]:
        if data.startswith(cls.PNG_SIGNATURE):
            if len(data) < 24 or data[12:16] != b"IHDR":
                raise ImageAssetError("PNG 缺少有效 IHDR")
            width, height = struct.unpack(">II", data[16:24])
            return "image/png", "png", width, height
        if data.startswith(b"\xff\xd8"):
            width, height = cls._jpeg_size(data)
            return "image/jpeg", "jpg", width, height
        raise ImageAssetError("图片格式无效，只支持 PNG 或 JPEG")

    @classmethod
    def _jpeg_size(cls, data: bytes) -> tuple[int, int]:
        offset = 2
        while offset < len(data):
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in {0xD8, 0xD9}:
                continue
            if marker == 0xDA:
                break
            if offset + 2 > len(data):
                break
            segment_length = int.from_bytes(data[offset:offset + 2], "big")
            if segment_length < 2 or offset + segment_length > len(data):
                break
            if marker in cls.JPEG_SOF_MARKERS:
                if segment_length < 7:
                    break
                height = int.from_bytes(data[offset + 3:offset + 5], "big")
                width = int.from_bytes(data[offset + 5:offset + 7], "big")
                if width and height:
                    return width, height
            offset += segment_length
        raise ImageAssetError("JPEG 缺少有效尺寸信息")
