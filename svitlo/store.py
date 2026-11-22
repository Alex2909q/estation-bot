"""Стан бота між запусками.

У GitHub Actions стан зберігається зашифрованим (AES-GCM, ключ з токена бота) в окремій гілці
svitlo-state цього ж репозиторію: так він не губиться, навіть якщо запуск обірвався посеред роботи.
Локально (для тестів) — у файлі svitlo/state.json.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("svitlo")
BRANCH = "svitlo-state"
FILE = "state.enc"


class Store:
    def __init__(self, secret: str, repo_dir: Path):
        self.key = hashlib.sha256(("svitlo-state:" + secret).encode()).digest()
        self.repo = repo_dir
        self.remote = bool(os.getenv("GITHUB_ACTIONS"))
        self.local = repo_dir / "svitlo" / "state.json"
        self.last_saved = ""

    # --- шифрування ---
    def _enc(self, data: bytes) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)
        return base64.b64encode(nonce + AESGCM(self.key).encrypt(nonce, data, None))

    def _dec(self, blob: bytes) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        raw = base64.b64decode(blob)
        return AESGCM(self.key).decrypt(raw[:12], raw[12:], None)

    def _git(self, *args, input_: bytes | None = None) -> bytes:
        env = dict(os.environ, GIT_AUTHOR_NAME="svitlo-bot", GIT_AUTHOR_EMAIL="svitlo@users.noreply.github.com",
                   GIT_COMMITTER_NAME="svitlo-bot", GIT_COMMITTER_EMAIL="svitlo@users.noreply.github.com")
        return subprocess.run(["git", "-C", str(self.repo), *args], input=input_, env=env,
                              capture_output=True, check=True, timeout=60).stdout

    # --- завантаження / збереження ---
    def load(self) -> dict:
        try:
            if self.remote:
                self._git("fetch", "-q", "--depth=1", "origin", BRANCH)
                blob = self._git("show", f"FETCH_HEAD:{FILE}")
                data = json.loads(self._dec(blob))
            else:
                data = json.loads(self.local.read_text(encoding="utf-8"))
            self.last_saved = json.dumps(data, sort_keys=True, ensure_ascii=False)
            return data
        except subprocess.CalledProcessError:
            log.info("Збереженого стану ще немає — старт з нуля.")
        except FileNotFoundError:
            pass
        except Exception as e:  # пошкоджений або іншим ключем
            log.warning("Не вдалося прочитати стан (%s) — старт з нуля.", type(e).__name__)
        return {}

    def save(self, state: dict) -> bool:
        dump = json.dumps(state, sort_keys=True, ensure_ascii=False)
        if dump == self.last_saved:
            return False
        try:
            if self.remote:
                with tempfile.NamedTemporaryFile(delete=False) as f:
                    f.write(self._enc(dump.encode()))
                    path = f.name
                blob = self._git("hash-object", "-w", path).decode().strip()
                os.unlink(path)
                tree = self._git("mktree", input_=f"100644 blob {blob}\t{FILE}\n".encode()).decode().strip()
                commit = self._git("commit-tree", tree, "-m", "state").decode().strip()
                self._git("push", "-q", "--force", "origin", f"{commit}:refs/heads/{BRANCH}")
            else:
                self.local.write_text(dump, encoding="utf-8")
            self.last_saved = dump
            return True
        except Exception as e:
            log.warning("Не вдалося зберегти стан: %s", e)
            return False
