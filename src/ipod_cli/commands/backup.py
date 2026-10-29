"""Comando para backup e restauração rápida do banco de dados do iPod."""

from __future__ import annotations

import os
import shutil
import zipfile
from datetime import datetime

from pathlib import Path
from typing import Optional

import typer

from ipod_cli.device import discover_ipod_mounts, inspect_ipod, is_ipod_mount
from ipod_cli.ui import console, print_banner, print_error, print_info, print_success, print_warning

app = typer.Typer(name="backup", help="Cria e restaura cópias de segurança do banco de dados do iPod.")


@app.callback(invoke_without_command=True)
def create_backup(
    ctx: typer.Context,
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Arquivo ou pasta de destino para salvar o backup (.zip).",
    ),
    mount_point: Optional[str] = typer.Option(
        None,
        "--mount",
        "-m",
        help="Letra ou caminho de montagem do iPod (ex: 'D:\\').",
    ),
) -> None:
    """Cria uma cópia compactada de segurança das pastas iTunes e Device do iPod."""
    if ctx.invoked_subcommand is not None:
        return

    print_banner()

    target_mount: Optional[Path] = None
    if mount_point:
        p = Path(mount_point)
        if not p.exists() or not is_ipod_mount(p):
            print_error(f"O caminho especificado não é um iPod válido: '{mount_point}'")
            raise typer.Exit(1)
        target_mount = p
    else:
        mounts = discover_ipod_mounts()
        if not mounts:
            print_error("Nenhum iPod detectado automaticamente.")
            raise typer.Exit(1)
        target_mount = mounts[0]

    dev = inspect_ipod(target_mount)
    print_info(f"Iniciando backup de [bold]{dev.device_name}[/bold] ({target_mount})...")

    # Define nome do arquivo de backup
    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    clean_name = "".join(c for c in dev.device_name if c.isalnum() or c in (" ", "_", "-")).strip() or "ipod"
    backup_filename = f"{clean_name}_backup_{now_str}.zip"

    if output is None:
        backup_dir = Path("ipod_backups")
        backup_dir.mkdir(parents=True, exist_ok=True)
        zip_path = backup_dir / backup_filename
    elif output.is_dir():
        output.mkdir(parents=True, exist_ok=True)
        zip_path = output / backup_filename
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        zip_path = output

    control_dir = target_mount / "iPod_Control"
    items_to_backup = ["iTunes", "Device"]

    file_count = 0
    total_bytes = 0

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for folder_name in items_to_backup:
            folder_path = control_dir / folder_name
            if not folder_path.exists():
                # Tenta minúsculo
                folder_path = control_dir / folder_name.lower()
            if not folder_path.exists():
                continue

            for root, _, files in os.walk(folder_path):
                for f in files:
                    file_p = Path(root) / f
                    arcname = f"iPod_Control/{Path(root).relative_to(control_dir)}/{f}".replace("\\", "/")
                    zipf.write(file_p, arcname)
                    file_count += 1
                    total_bytes += file_p.stat().st_size

    mb_size = total_bytes / (1024 * 1024)
    print_success(
        f"Backup criado com sucesso em: [bold cyan]{zip_path}[/bold cyan] "
        f"({file_count} arquivos, {mb_size:.2f} MB)"
    )


@app.command(name="restore")
def restore_backup(
    backup_file: Path = typer.Argument(
        ...,
        help="Arquivo .zip do backup a ser restaurado.",
    ),
    mount_point: Optional[str] = typer.Option(
        None,
        "--mount",
        "-m",
        help="Letra ou caminho de montagem do iPod (ex: 'D:\\').",
    ),
) -> None:
    """Restaura as pastas iTunes e Device a partir de um arquivo de backup .zip."""
    print_banner()

    if not backup_file.exists():
        print_error(f"Arquivo de backup não encontrado: '{backup_file}'")
        raise typer.Exit(1)

    target_mount: Optional[Path] = None
    if mount_point:
        p = Path(mount_point)
        if not p.exists() or not is_ipod_mount(p):
            print_error(f"O caminho especificado não é um iPod válido: '{mount_point}'")
            raise typer.Exit(1)
        target_mount = p
    else:
        mounts = discover_ipod_mounts()
        if not mounts:
            print_error("Nenhum iPod detectado automaticamente.")
            raise typer.Exit(1)
        target_mount = mounts[0]

    dev = inspect_ipod(target_mount)
    print_warning(
        f"Atenção: A restauração irá sobrescrever o banco de dados atual de [bold]{dev.device_name}[/bold] ({target_mount})!"
    )
    if not typer.confirm("Tem certeza de que deseja restaurar este backup?"):
        print_info("Operação cancelada pelo usuário.")
        return

    control_dir = target_mount / "iPod_Control"
    print_info(f"Extraindo arquivos para [bold]{control_dir}[/bold]...")

    with zipfile.ZipFile(backup_file, "r") as zipf:
        for member in zipf.infolist():
            # Extrai apenas membros pertencentes a iPod_Control
            if member.filename.startswith("iPod_Control/"):
                rel_path = member.filename[len("iPod_Control/"):]
                if not rel_path:
                    continue
                dest = control_dir / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zipf.open(member) as src_f, open(dest, "wb") as dst_f:
                    shutil.copyfileobj(src_f, dst_f)

    print_success("Restauração concluída com sucesso! O banco de dados do iPod foi restaurado.")
