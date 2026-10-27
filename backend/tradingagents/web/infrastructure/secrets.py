"""Secret references only; production never falls back to plaintext files."""

from threading import RLock
from typing import Protocol


class SecretStoreError(Exception):
    pass


class SecretStore(Protocol):
    def set(self, reference: str, value: str) -> None: ...
    def get(self, reference: str) -> str | None: ...
    def delete(self, reference: str) -> None: ...


class KeyringSecretStore:
    def __init__(self, service_name: str = "TradingAgents.Web.Models"):
        self.service_name = service_name

    @staticmethod
    def _backend():
        import keyring

        backend = keyring.get_keyring()
        module = type(backend).__module__
        if module not in {"keyring.backends.macOS", "keyring.backends.Windows",
                          "keyring.backends.SecretService", "keyring.backends.kwallet"}:
            raise SecretStoreError("只允许系统密钥库，不使用文件或未知回退存储。")
        return backend

    def set(self, reference: str, value: str) -> None:
        try:
            self._backend().set_password(self.service_name, reference, value)
        except Exception:
            raise SecretStoreError("系统密钥存储不可用；未将凭据写入文件。") from None

    def get(self, reference: str) -> str | None:
        try:
            return self._backend().get_password(self.service_name, reference)
        except Exception:
            raise SecretStoreError("无法读取系统密钥存储。") from None

    def delete(self, reference: str) -> None:
        try:
            backend = self._backend()
            if backend.get_password(self.service_name, reference) is not None:
                backend.delete_password(self.service_name, reference)
        except Exception:
            raise SecretStoreError("无法删除系统密钥引用。") from None


class MemorySecretStore:
    """Explicitly injected in tests; never selected as a production fallback."""

    def __init__(self):
        self._values: dict[str, str] = {}
        self._lock = RLock()

    def set(self, reference: str, value: str) -> None:
        with self._lock:
            self._values[reference] = value

    def get(self, reference: str) -> str | None:
        with self._lock:
            return self._values.get(reference)

    def delete(self, reference: str) -> None:
        with self._lock:
            self._values.pop(reference, None)
