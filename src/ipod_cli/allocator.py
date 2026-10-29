"""Alocador de caminhos de arquivos de música compatível com a estrutura de diretórios do iPod.

O firmware do iPod organiza faixas em diretórios iPod_Control/Music/F00 a Fxx
com nomes de quatro letras em maiúsculas (ex: ABCD.m4a, BJFS.mp3).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath
from secrets import randbelow
from threading import Lock

_NAMES_PER_DIRECTORY = 26**4  # 456.976 combinações por pasta Fxx
_TRACK_PATH = re.compile(
    r"ipod_control/music/f([0-9]{2,})/([a-z]{4})(?:\.[^/]+)?", re.ASCII
)
_EXTENSIONS = frozenset({"mp3", "m4a", "m4b", "m4v", "mp4", "wav", "aif", "aiff"})


class MusicPathAllocator:
    """Aloca caminhos Fxx/ABCD.ext sem conflito com arquivos existentes."""

    def __init__(
        self,
        directory_count: int = 50,
        occupied_paths: Iterable[str] = (),
        *,
        exists: Callable[[str], bool] | None = None,
    ) -> None:
        if directory_count < 1:
            raise ValueError("O iPod requer pelo menos um diretório de música.")
        self._directory_count = directory_count
        self._capacity = directory_count * _NAMES_PER_DIRECTORY
        self._cursor = randbelow(self._capacity)
        self._reserved: set[int] = set()
        self._exists = exists
        self._lock = Lock()

        for path in occupied_paths:
            normalized = (
                PurePosixPath(path.replace("\\", "/").replace(":", "/"))
                .as_posix()
                .lstrip("/")
                .casefold()
            )
            match = _TRACK_PATH.fullmatch(normalized)
            if match is None:
                continue
            directory = int(match[1])
            if directory >= directory_count or match[1] != f"{directory:02d}":
                continue
            name = 0
            for letter in match[2]:
                name = name * 26 + ord(letter) - ord("a")
            if name < _NAMES_PER_DIRECTORY:
                self._reserved.add(name * directory_count + directory)

    def allocate(self, extension: str) -> str:
        """Seleciona e reserva um nome único de 4 letras."""
        clean_ext = extension.lstrip(".").casefold()
        if clean_ext not in _EXTENSIONS:
            raise ValueError(f"Extensão de arquivo não suportada pelo iPod: {extension}")

        with self._lock:
            while len(self._reserved) < self._capacity:
                slot = self._cursor
                self._cursor = (slot + 1) % self._capacity
                if slot in self._reserved:
                    continue
                self._reserved.add(slot)
                name, directory = divmod(slot, self._directory_count)
                letters: list[str] = []
                for _ in range(4):
                    name, digit = divmod(name, 26)
                    letters.append(chr(ord("A") + digit))
                stem = "".join(reversed(letters))
                path = f"iPod_Control/Music/F{directory:02d}/{stem}.{clean_ext}"
                if self._exists is None or not self._exists(path):
                    return path

        raise RuntimeError(
            "Limite de armazenamento de nomes esgotado na partição do iPod."
        )


def count_music_directories(ipod_mount: Path) -> int:
    """Detecta o número de pastas Fxx em iPod_Control/Music."""
    music_dir = ipod_mount / "iPod_Control" / "Music"
    if not music_dir.exists():
        return 50
    f_dirs = [
        d for d in music_dir.iterdir()
        if d.is_dir() and re.match(r"^F\d{2}$", d.name, re.IGNORECASE)
    ]
    return max(len(f_dirs), 20)
