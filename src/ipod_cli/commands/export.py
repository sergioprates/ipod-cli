"""Comando para exportar músicas do iPod para o computador de forma organizada."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Optional

import typer
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeRemainingColumn

from ipod_cli.commands.tracks import _load_library
from ipod_cli.ui import console, print_banner, print_error, print_info, print_success, print_warning

app = typer.Typer(name="export", help="Exporta músicas do iPod recuperando nomes e estrutura de pastas.")

_INVALID_CHARS = re.compile(r'[\\/*?:"<>|]')


def _sanitize(name: str) -> str:
    """Substitui caracteres inválidos para nomes de arquivos em qualquer SO."""
    clean = _INVALID_CHARS.sub("_", name).strip()
    return clean or "Desconhecido"


def _resolve_ipod_file(mount_path: Path, location: str) -> Optional[Path]:
    """Resolve o caminho relativo do iPod para o arquivo físico no disco."""
    if not location:
        return None
    # Converte caminhos estilo Mac ":iPod_Control:Music:F00:xyz.mp3" ou "iPod_Control/Music/F00/xyz.mp3"
    norm = location.replace(":", "/").strip("/")
    direct = mount_path / norm
    if direct.exists():
        return direct

    # Busca tolerante a maiúsculas/minúsculas
    parts = norm.split("/")
    cur = mount_path
    for part in parts:
        found = False
        try:
            for child in cur.iterdir():
                if child.name.lower() == part.lower():
                    cur = child
                    found = True
                    break
        except Exception:
            return None
        if not found:
            return None
    return cur if cur.is_file() else None


def export_tracks(
    destination: Path = typer.Argument(
        ...,
        help="Pasta de destino no computador onde as músicas serão salvas.",
    ),

    mount_point: Optional[str] = typer.Option(
        None,
        "--mount",
        "-m",
        help="Letra ou caminho de montagem do iPod (ex: 'D:\\').",
    ),
    artist: Optional[str] = typer.Option(
        None,
        "--artist",
        "-a",
        help="Exporta apenas faixas do artista informado.",
    ),
    album: Optional[str] = typer.Option(
        None,
        "--album",
        "-b",
        help="Exporta apenas faixas do álbum informado.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Simula a exportação exibindo o que seria copiado sem transferir arquivos.",
    ),
) -> None:
    """Extrai as músicas do iPod, renomeando os arquivos internos para 'Artista/Álbum/Faixa.ext'."""
    print_banner()

    mount_path, library = _load_library(mount_point)
    tracks = library.snapshot.tracks

    # Filtros opcionais
    if artist:
        q = artist.lower()
        tracks = tuple(t for t in tracks if q in (t.artist or "").lower())
    if album:
        q = album.lower()
        tracks = tuple(t for t in tracks if q in (t.album or "").lower())

    if not tracks:
        print_warning("Nenhuma faixa encontrada para exportação com os filtros aplicados.")
        return

    print_info(f"Preparando exportação de [bold]{len(tracks)}[/bold] música(s) para [bold]{destination}[/bold]...")

    if dry_run:
        print_warning("Modo DRY-RUN ativo. Nenhum arquivo será copiado.")

    destination.mkdir(parents=True, exist_ok=True)

    copied = 0
    missing = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Exportando...", total=len(tracks))

        for t in tracks:
            progress.update(task, description=f"[cyan]Exportando: {t.title[:30]}...")
            loc = t.metadata.location
            source_file = _resolve_ipod_file(mount_path, loc)

            if not source_file:
                missing += 1
                progress.advance(task)
                continue

            ext = source_file.suffix or ".mp3"
            clean_artist = _sanitize(t.artist or "Artista Desconhecido")
            clean_album = _sanitize(t.album or "Álbum Desconhecido")
            track_prefix = f"{t.track_number:02d} - " if t.track_number else ""
            clean_title = _sanitize(f"{track_prefix}{t.title or 'Faixa Sem Titulo'}")

            dest_folder = destination / clean_artist / clean_album
            dest_file = dest_folder / f"{clean_title}{ext}"

            if not dry_run:
                dest_folder.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(source_file, dest_file)
                except Exception as e:
                    console.print(f"[dim red]Falha ao copiar '{t.title}': {e}[/dim red]")
                    missing += 1
                    progress.advance(task)
                    continue

            copied += 1
            progress.advance(task)

    if dry_run:
        print_info(f"Simulação concluída: {copied} faixas seriam exportadas ({missing} arquivos não localizados).")
    else:
        print_success(f"Exportação concluída com sucesso! [bold]{copied}[/bold] faixas salvas em '{destination}'.")
        if missing > 0:
            print_warning(f"{missing} faixa(s) não puderam ser localizadas no disco do iPod.")
