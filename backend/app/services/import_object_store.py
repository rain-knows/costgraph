"""本地内容寻址对象存储。

接口形态与 S3 的 ``put_object`` / ``get_object`` 对齐：调用方只持有
``object_key``，从不由存储层把原始载荷写回 HTTP 响应。``object_key`` 由内容
SHA-256 派生，因此相同内容天然幂等，且读取时可以重新校验完整性。

写入通过「同目录临时文件 + ``os.replace``」保证原子性；读取时重新计算
SHA-256 并与 ``object_key`` 中记录的值比对，不一致直接报错。
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

OBJECT_KEY_PREFIX = "imports"
_KEY_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class ImportObjectStoreError(RuntimeError):
    """对象存储失败基类。"""


class ImportObjectNotFoundError(ImportObjectStoreError):
    """请求的 ``object_key`` 不存在。"""


class ImportObjectIntegrityError(ImportObjectStoreError):
    """读回内容与 ``object_key`` 记录的 SHA-256 不一致。"""


@dataclass(frozen=True)
class ObjectRef:
    """对象写入结果的不可变引用。"""

    object_key: str
    size: int
    sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "object_key": self.object_key,
            "size": self.size,
            "sha256": self.sha256,
        }


class LocalImportObjectStore:
    """受控目录下的内容寻址对象存储。"""

    def __init__(self, root: str | Path | None = None) -> None:
        if root is None:
            root = os.environ.get("IMPORT_OBJECT_STORE_ROOT") or str(
                Path(tempfile.gettempdir()) / "costgraph-import-objects"
            )
        self._root = Path(root).expanduser().resolve()

    @property
    def root(self) -> Path:
        return self._root

    def put(self, data: bytes) -> ObjectRef:
        digest = hashlib.sha256(data).hexdigest()
        object_key = self._object_key(digest)
        target = self._path_for_key(object_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            handle, temp_name = tempfile.mkstemp(
                dir=str(target.parent), prefix=".tmp-", suffix=".part"
            )
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_name, target)
            except BaseException:
                Path(temp_name).unlink(missing_ok=True)
                raise
        return ObjectRef(object_key=object_key, size=len(data), sha256=digest)

    def get(self, object_key: str) -> bytes:
        path = self._path_for_key(object_key)
        try:
            data = path.read_bytes()
        except FileNotFoundError as exc:
            raise ImportObjectNotFoundError(f"对象不存在：{object_key}") from exc
        digest = hashlib.sha256(data).hexdigest()
        if digest != self._key_digest(object_key):
            raise ImportObjectIntegrityError(
                f"对象内容与 object_key 不一致：{object_key}"
            )
        return data

    def _object_key(self, digest: str) -> str:
        return f"{OBJECT_KEY_PREFIX}/{digest[:2]}/{digest}"

    @staticmethod
    def _key_digest(object_key: str) -> str:
        return object_key.rsplit("/", 1)[-1]

    def _path_for_key(self, object_key: str) -> Path:
        digest = self._key_digest(object_key)
        if not _KEY_DIGEST.match(digest):
            raise ImportObjectStoreError(f"非法 object_key：{object_key}")
        if object_key != self._object_key(digest):
            raise ImportObjectStoreError(f"非法 object_key：{object_key}")
        return self._root / OBJECT_KEY_PREFIX / digest[:2] / digest
