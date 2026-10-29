"""Comando para adicionar músicas ao iPod com conversão de áudio automática."""

from __future__ import annotations

import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import List, Optional

import typer
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn, TimeRemainingColumn
from rich.table import Table

from iPodDB.library.media import AudioEncoding, prepared_audio
from iPodDB.library.models import Track, TrackMetadata
from iPodDB.library.writing import FileDependency, PreparedMedia, WriteResources
from ipod_cli.allocator import MusicPathAllocator, count_music_directories
from ipod_cli.device import discover_ipod_mounts, inspect_ipod, is_ipod_mount
from ipod_cli.metadata import AudioMetadata, extract_metadata
from ipod_cli.storage_ops import commit_transaction, load_ipod_library
from ipod_cli.transcode import is_ffmpeg_available, needs_transcoding, transcode_to_alac
from ipod_cli.ui import console, print_error, print_info, print_success, print_warning

SUPPORTED_AUDIO_EXTS = {
    ".mp3", ".m4a", ".aac", ".wav", ".aif", ".aiff",
    ".flac", ".ogg", ".opus", ".wma", ".ape", ".wv"
}


def _collect_files(paths: list[str]) -> list[Path]:
    """Coleta todos os arquivos de áudio válidos de arquivos e diretórios."""
    results: list[Path] = []
    for p_str in paths:
        p = Path(p_str)
        if p.is_file() and p.suffix.lower() in SUPPORTED_AUDIO_EXTS:
            results.append(p.resolve())
        elif p.is_dir():
            for item in p.rglob("*"):
                if item.is_file() and item.suffix.lower() in SUPPORTED_AUDIO_EXTS:
                    results.append(item.resolve())
    return sorted(list(set(results)))


def add_tracks(
    paths: List[str] = typer.Argument(
        ...,
        help="Arquivos ou diretórios de música a serem adicionados ao iPod.",
    ),
    mount_point: Optional[str] = typer.Option(
        None,
        "--mount",
        "-m",
        help="Letra da unidade ou ponto de montagem do iPod (ex: 'D:\\').",
    ),
    force_alac: bool = typer.Option(
        False,
        "--force-alac",
        help="Força a conversão de todos os arquivos para ALAC 44.1kHz (iPod 5.5 Wolfson DAC).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Simula a adição sem copiar arquivos nem modificar o iPod.",
    ),
) -> None:
    """Adiciona músicas ao iPod, convertendo automaticamente formatos não suportados."""
    # 1. Identifica o ponto de montagem do iPod
    target_mount: Optional[Path] = None
    if mount_point:
        p = Path(mount_point)
        if not is_ipod_mount(p):
            print_error(f"O caminho '{mount_point}' não é um iPod válido.")
            raise typer.Exit(1)
        target_mount = p
    else:
        mounts = discover_ipod_mounts()
        if not mounts:
            print_error("Nenhum iPod detectado automaticamente.")
            raise typer.Exit(1)
        target_mount = mounts[0]

    # 2. Coleta arquivos de áudio
    input_files = _collect_files(paths)
    if not input_files:
        print_warning("Nenhum arquivo de áudio suportado encontrado nos caminhos fornecidos.")
        raise typer.Exit(0)

    print_info(f"Encontrado(s) [bold]{len(input_files)}[/bold] arquivo(s) de áudio para processar.")

    # 3. Carrega o banco de dados do iPod
    try:
        lib = load_ipod_library(target_mount)
    except Exception as e:
        print_error(f"Erro ao carregar banco de dados do iPod: {e}")
        raise typer.Exit(1)

    # 4. Configura o alocador de caminhos do iPod
    existing_locations = [t.metadata.location for t in lib.snapshot.tracks if t.metadata.location]
    dir_count = count_music_directories(target_mount)
    allocator = MusicPathAllocator(directory_count=dir_count, occupied_paths=existing_locations)

    # Mapeamento de capa existente por Álbum e Artista
    album_artwork_map: dict[tuple[str, str], int] = {}
    for t in lib.snapshot.tracks:
        if t.artwork_id > 0 and t.album:
            key = (t.artist.casefold().strip(), t.album.casefold().strip())
            album_artwork_map[key] = t.artwork_id

    # 5. Processamento dos arquivos
    temp_dir = Path(tempfile.mkdtemp(prefix="ipod_add_"))
    files_to_copy: list[tuple[Path, Path]] = []
    prepared_medias: list[PreparedMedia] = []
    new_tracks: list[Track] = []

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Processando músicas...", total=len(input_files))

            for idx, src_file in enumerate(input_files, start=1):
                progress.update(task, description=f"[cyan]Processando ({idx}/{len(input_files)}):[/cyan] {src_file.name}")

                # Verifica se precisa transcodificar
                should_transcode = force_alac or needs_transcoding(src_file)
                process_file = src_file

                if should_transcode:
                    if not is_ffmpeg_available():
                        print_error("FFmpeg não encontrado no sistema. Necessário para transcodificação.")
                        raise typer.Exit(1)
                    alac_temp = temp_dir / f"{src_file.stem}_{idx}.m4a"
                    transcode_to_alac(src_file, alac_temp)
                    process_file = alac_temp

                # Extrai metadados do arquivo final a ser adicionado
                meta = extract_metadata(process_file)
                ext = process_file.suffix.lstrip(".").lower()
                ipod_rel_path = allocator.allocate(ext)
                ipod_abs_dst = target_mount / ipod_rel_path.replace("/", os.sep)

                # Verifica se herda artwork de álbum existente
                album_key = (meta.artist.casefold().strip(), meta.album.casefold().strip())
                inherited_artwork_id = album_artwork_map.get(album_key, 0)

                temp_track_id = -idx
                file_dep = FileDependency(ipod_rel_path, meta.size_bytes, meta.sha256)
                media = prepared_audio(temp_track_id, file_dep, meta.encoding)
                prepared_medias.append(media)

                track_meta = TrackMetadata(
                    location=ipod_rel_path,
                    sample_rate_hz=meta.sample_rate_hz,
                    file_format=meta.file_format,
                    disc_number=meta.disc_number,
                    total_discs=meta.total_discs,
                    total_tracks=meta.total_tracks,
                    composer=meta.composer,
                )

                new_track = Track(
                    track_id=temp_track_id,
                    title=meta.title,
                    artist=meta.artist,
                    album=meta.album,
                    album_artist=meta.album_artist,
                    length_ms=meta.length_ms,
                    size_bytes=meta.size_bytes,
                    bitrate_kbps=meta.bitrate_kbps,
                    year=meta.year,
                    track_number=meta.track_number,
                    genre=meta.genre,
                    artwork_id=inherited_artwork_id,
                    metadata=track_meta,
                )
                new_tracks.append(new_track)
                files_to_copy.append((process_file, ipod_abs_dst))
                progress.advance(task)

        # 6. Preparação no iPodDB
        print_info("Preparando atualização no banco de dados do iPod...")
        desired_snapshot = replace(lib.snapshot, tracks=(*lib.snapshot.tracks, *new_tracks))
        draft = lib.begin_draft(desired_snapshot)
        plan = lib.analyze(draft)
        write_result = lib.prepare(
            plan,
            WriteResources(media=tuple(prepared_medias), pending_playback_sidecars=False),
        )

        if write_result.issues:
            print_error(f"Validação da biblioteca falhou com {len(write_result.issues)} problema(s):")
            for issue in write_result.issues:
                console.print(f"  [red]• {issue.code}[/red]: {issue.message} ({issue.detail or ''})")
            raise typer.Exit(1)

        if write_result.prepared is None:
            print_error("A biblioteca preparada não retornou dados.")
            raise typer.Exit(1)

        # 7. Exibição de resumo
        table = Table(title="Músicas Prontas para Adicionar", border_style="green")
        table.add_column("#", justify="right", style="dim")
        table.add_column("Título", style="bold white")
        table.add_column("Artista", style="cyan")
        table.add_column("Álbum", style="magenta")
        table.add_column("Formato", style="yellow")
        table.add_column("Destino no iPod", style="green")

        for idx, t in enumerate(new_tracks, start=1):
            table.add_row(
                str(idx),
                t.title,
                t.artist,
                t.album,
                t.metadata.file_format,
                t.metadata.location,
            )

        console.print(table)

        if dry_run:
            print_warning("[Modo Simulação] Nenhuma alteração gravada no iPod.")
            return

        # 8. Gravação transacional
        print_info(f"Copiando arquivos e atualizando iTunesDB no iPod ({target_mount})...")
        commit_transaction(
            target_mount,
            write_result.prepared.itunes,
            files_to_copy,
        )

        print_success(
            f"Sucesso! [bold green]{len(new_tracks)}[/bold green] música(s) adicionada(s) à biblioteca do iPod!"
        )

    finally:
        # Limpa arquivos temporários de transcodificação
        if temp_dir.exists():
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
