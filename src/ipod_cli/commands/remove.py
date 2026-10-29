"""Comando para remover músicas do iPod com segurança e limpeza de arquivos."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import List, Optional

import typer
from rich.table import Table

from iPodDB.library.models import Track
from iPodDB.library.writing import WriteResources
from ipod_cli.device import discover_ipod_mounts, is_ipod_mount
from ipod_cli.storage_ops import commit_transaction, load_ipod_library
from ipod_cli.ui import console, print_error, print_info, print_success, print_warning


def remove_tracks(
    track_ids: Optional[List[int]] = typer.Argument(
        None,
        help="IDs numéricos das faixas a serem removidas.",
    ),
    mount_point: Optional[str] = typer.Option(
        None,
        "--mount",
        "-m",
        help="Letra da unidade ou ponto de montagem do iPod.",
    ),
    search: Optional[str] = typer.Option(
        None,
        "--search",
        "-s",
        help="Termo de busca para encontrar e remover faixas por título, artista ou álbum.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Confirma a remoção sem solicitar confirmação interativa.",
    ),
    keep_files: bool = typer.Option(
        False,
        "--keep-files",
        help="Mantém os arquivos físicos no disco do iPod, removendo apenas do banco de dados.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Simula a remoção sem apagar arquivos nem modificar o iPod.",
    ),
) -> None:
    """Remove faixas da biblioteca do iPod e apaga seus arquivos físicos correspondentes."""
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

    # 2. Carrega biblioteca
    try:
        lib = load_ipod_library(target_mount)
    except Exception as e:
        print_error(f"Erro ao carregar banco de dados do iPod: {e}")
        raise typer.Exit(1)

    all_tracks = lib.snapshot.tracks
    target_tracks: list[Track] = []

    # 3. Filtra faixas por ID ou busca
    if track_ids:
        id_set = set(track_ids)
        target_tracks = [t for t in all_tracks if t.track_id in id_set]
    elif search:
        query = search.casefold().strip()
        target_tracks = [
            t for t in all_tracks
            if query in t.title.casefold()
            or query in t.artist.casefold()
            or query in t.album.casefold()
        ]
    else:
        print_error("Especifique pelo menos um ID de faixa ou use a opção --search.")
        raise typer.Exit(1)

    if not target_tracks:
        print_warning("Nenhuma faixa encontrada com os critérios informados.")
        raise typer.Exit(0)

    # 4. Tabela de confirmação
    table = Table(title=f"Faixas Selecionadas para Remoção ({len(target_tracks)})", border_style="red")
    table.add_column("ID", justify="right", style="cyan")
    table.add_column("Título", style="bold white")
    table.add_column("Artista", style="green")
    table.add_column("Álbum", style="magenta")
    table.add_column("Arquivo no iPod", style="dim")

    for t in target_tracks:
        table.add_row(
            str(t.track_id),
            t.title,
            t.artist,
            t.album,
            t.metadata.location,
        )

    console.print(table)

    if dry_run:
        print_warning("[Modo Simulação] Nenhuma faixa ou arquivo foi excluído.")
        return

    if not yes:
        confirmed = typer.confirm(
            f"Tem certeza que deseja remover permanentemente {len(target_tracks)} faixa(s) do iPod?",
            default=False,
        )
        if not confirmed:
            print_info("Operação cancelada pelo usuário.")
            return

    # 5. Prepara exclusão com delete_omissions=True
    remove_ids = {t.track_id for t in target_tracks}
    remaining_tracks = tuple(t for t in all_tracks if t.track_id not in remove_ids)
    desired_snapshot = replace(lib.snapshot, tracks=remaining_tracks)

    draft = lib.begin_draft(desired_snapshot, delete_omissions=True)
    plan = lib.analyze(draft)
    write_result = lib.prepare(plan, WriteResources(pending_playback_sidecars=False))

    if write_result.issues:
        print_error(f"Validação da remoção falhou com {len(write_result.issues)} problema(s):")
        for issue in write_result.issues:
            console.print(f"  [red]• {issue.code}[/red]: {issue.message} ({issue.detail or ''})")
        raise typer.Exit(1)

    if write_result.prepared is None:
        print_error("A biblioteca preparada não retornou dados.")
        raise typer.Exit(1)

    # 6. Identifica arquivos físicos a apagar
    files_to_delete: list[Path] = []
    if not keep_files:
        for t in target_tracks:
            if t.metadata.location:
                abs_f = target_mount / t.metadata.location.replace("/", os.sep)
                files_to_delete.append(abs_f)

    # 7. Commit transacional
    print_info("Gravando alterações no banco de dados e removendo arquivos do iPod...")
    commit_transaction(
        target_mount,
        write_result.prepared.itunes,
        files_to_copy=[],
        files_to_delete=files_to_delete,
    )

    print_success(
        f"Sucesso! [bold red]{len(target_tracks)}[/bold red] faixa(s) removida(s) da biblioteca do iPod."
    )
