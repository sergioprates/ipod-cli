"""Motor de sincronização inteligente de pasta local para o iPod."""

from __future__ import annotations

import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Optional

import typer
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
from rich.table import Table

from iPodDB.library.media import prepared_audio
from iPodDB.library.models import Track, TrackMetadata
from iPodDB.library.writing import FileDependency, PreparedMedia, WriteResources
from ipod_cli.allocator import MusicPathAllocator, count_music_directories
from ipod_cli.device import discover_ipod_mounts, is_ipod_mount
from ipod_cli.metadata import AudioMetadata, extract_metadata
from ipod_cli.storage_ops import commit_transaction, load_ipod_library
from ipod_cli.transcode import is_ffmpeg_available, needs_transcoding, transcode_to_alac
from ipod_cli.ui import console, print_error, print_info, print_success, print_warning

SUPPORTED_AUDIO_EXTS = {
    ".mp3", ".m4a", ".aac", ".wav", ".aif", ".aiff",
    ".flac", ".ogg", ".opus", ".wma", ".ape", ".wv"
}


def _norm(text: str) -> str:
    """Normaliza texto para comparação flexível."""
    return "".join(c for c in text.casefold() if c.isalnum() or c.isspace()).strip()


def sync_library(
    source_dir: Path = typer.Argument(
        ...,
        help="Pasta local no computador contendo os arquivos de música a sincronizar.",
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
    ),
    mount_point: Optional[str] = typer.Option(
        None,
        "--mount",
        "-m",
        help="Letra da unidade ou ponto de montagem do iPod.",
    ),
    delete: bool = typer.Option(
        False,
        "--delete",
        help="Remove do iPod músicas que não existem mais na pasta local de origem.",
    ),
    force_alac: bool = typer.Option(
        False,
        "--force-alac",
        help="Força transcodificação de todas as músicas locais para ALAC 44.1kHz.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Simula a sincronização sem alterar o iPod.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Executa a sincronização sem solicitar confirmação interativa.",
    ),
) -> None:
    """Sincroniza uma pasta de músicas local com o iPod de forma inteligente."""
    # 1. Ponto de montagem
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

    # 2. Carrega biblioteca do iPod
    clean_mount = str(target_mount).rstrip("/\\")
    print_info(f"Lendo biblioteca do iPod em [bold]{clean_mount}[/bold]...")
    try:
        lib = load_ipod_library(target_mount)
    except Exception as e:
        print_error(f"Erro ao carregar iPod: {e}")
        raise typer.Exit(1)

    ipod_tracks = lib.snapshot.tracks
    # Cria índice por (artista, título)
    ipod_index: dict[tuple[str, str], Track] = {}
    for t in ipod_tracks:
        key = (_norm(t.artist), _norm(t.title))
        ipod_index[key] = t

    # 3. Escaneia pasta local
    print_info(f"Escaneando pasta local [bold]{source_dir}[/bold]...")
    local_files: list[Path] = []
    for item in source_dir.rglob("*"):
        if item.is_file() and item.suffix.lower() in SUPPORTED_AUDIO_EXTS:
            local_files.append(item.resolve())

    if not local_files:
        print_warning(f"Nenhum arquivo de áudio suportado encontrado em '{source_dir}'.")
        raise typer.Exit(0)

    print_info(f"Encontrados [bold]{len(local_files)}[/bold] arquivos de áudio na pasta local.")

    # 4. Compara biblioteca local com iPod
    files_to_add: list[Path] = []
    matched_ipod_ids: set[int] = set()

    for f in local_files:
        try:
            m = extract_metadata(f)
            key = (_norm(m.artist), _norm(m.title))
            if key in ipod_index:
                matched_ipod_ids.add(ipod_index[key].track_id)
            else:
                files_to_add.append(f)
        except Exception:
            files_to_add.append(f)

    tracks_to_delete: list[Track] = []
    if delete:
        tracks_to_delete = [t for t in ipod_tracks if t.track_id not in matched_ipod_ids]

    # 5. Tabela de ações
    summary_table = Table(title="Resumo do Plano de Sincronização", border_style="cyan")
    summary_table.add_column("Operação", style="bold")
    summary_table.add_column("Quantidade", justify="right", style="bold")
    summary_table.add_column("Descrição")

    summary_table.add_row(
        "[green]+ Adicionar[/green]",
        str(len(files_to_add)),
        "Músicas locais novas a copiar/converter para o iPod",
    )
    if delete:
        summary_table.add_row(
            "[red]- Remover[/red]",
            str(len(tracks_to_delete)),
            "Músicas no iPod que não estão mais na pasta local",
        )
    else:
        summary_table.add_row(
            "[dim]= Preservar[/dim]",
            str(len(ipod_tracks) - len(matched_ipod_ids)),
            "Músicas extras no iPod mantidas (use --delete para remover)",
        )
    summary_table.add_row(
        "[blue]= Sincronizadas[/blue]",
        str(len(matched_ipod_ids)),
        "Músicas que já estão sincronizadas",
    )

    console.print(summary_table)

    if not files_to_add and not tracks_to_delete:
        print_success("A biblioteca do iPod já está perfeitamente sincronizada!")
        return

    if dry_run:
        print_warning("[Modo Simulação] Nenhuma alteração aplicada.")
        return

    if not yes:
        if not typer.confirm("Deseja aplicar a sincronização no iPod?", default=True):
            print_info("Sincronização cancelada.")
            return

    # 6. Alocador e estruturas de dados
    existing_locations = [t.metadata.location for t in ipod_tracks if t.metadata.location]
    dir_count = count_music_directories(target_mount)
    allocator = MusicPathAllocator(directory_count=dir_count, occupied_paths=existing_locations)

    temp_dir = Path(tempfile.mkdtemp(prefix="ipod_sync_"))
    files_to_copy: list[tuple[Path, Path]] = []
    prepared_medias: list[PreparedMedia] = []
    new_tracks: list[Track] = []

    try:
        # Processamento das adições
        if files_to_add:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TimeRemainingColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("Processando novas músicas...", total=len(files_to_add))

                for idx, src_file in enumerate(files_to_add, start=1):
                    progress.update(task, description=f"[cyan]Preparando ({idx}/{len(files_to_add)}):[/cyan] {src_file.name}")

                    should_transcode = force_alac or needs_transcoding(src_file)
                    process_file = src_file

                    if should_transcode:
                        if not is_ffmpeg_available():
                            print_error("FFmpeg não encontrado no sistema. Necessário para transcodificação.")
                            raise typer.Exit(1)
                        alac_temp = temp_dir / f"{src_file.stem}_{idx}.m4a"
                        transcode_to_alac(src_file, alac_temp)
                        process_file = alac_temp

                    meta = extract_metadata(process_file)
                    ext = process_file.suffix.lstrip(".").lower()
                    ipod_rel_path = allocator.allocate(ext)
                    ipod_abs_dst = target_mount / ipod_rel_path.replace("/", os.sep)

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

                    new_tracks.append(
                        Track(
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
                            metadata=track_meta,
                        )
                    )
                    files_to_copy.append((process_file, ipod_abs_dst))
                    progress.advance(task)

        # Prepara snapshot desejado
        delete_ids = {t.track_id for t in tracks_to_delete}
        base_tracks = tuple(t for t in ipod_tracks if t.track_id not in delete_ids)
        desired_snapshot = replace(lib.snapshot, tracks=(*base_tracks, *new_tracks))

        print_info("Validando e preparando dados do iPod...")
        draft = lib.begin_draft(desired_snapshot, delete_omissions=bool(tracks_to_delete))
        plan = lib.analyze(draft)
        write_result = lib.prepare(
            plan,
            WriteResources(media=tuple(prepared_medias), pending_playback_sidecars=False),
        )

        if write_result.issues:
            print_error(f"Validação da sincronização falhou com {len(write_result.issues)} problema(s):")
            for issue in write_result.issues:
                console.print(f"  [red]• {issue.code}[/red]: {issue.message} ({issue.detail or ''})")
            raise typer.Exit(1)

        if write_result.prepared is None:
            print_error("A biblioteca preparada não retornou dados.")
            raise typer.Exit(1)

        # Arquivos a apagar
        files_to_delete: list[Path] = []
        for t in tracks_to_delete:
            if t.metadata.location:
                files_to_delete.append(target_mount / t.metadata.location.replace("/", os.sep))

        # Commit transacional
        print_info(f"Gravando sincronização no iPod ({target_mount})...")
        commit_transaction(
            target_mount,
            write_result.prepared.itunes,
            files_to_copy,
            files_to_delete,
        )

        print_success(
            f"Sincronização concluída com sucesso! "
            f"[bold green]+{len(new_tracks)}[/bold green] adicionada(s), "
            f"[bold red]-{len(tracks_to_delete)}[/bold red] removida(s)."
        )

    finally:
        if temp_dir.exists():
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
