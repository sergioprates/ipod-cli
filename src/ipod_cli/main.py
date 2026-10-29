"""Ponto de entrada principal da CLI ipod-sync."""

import logging
from pathlib import Path
from typing import Optional

import typer
from rich.panel import Panel
from rich.table import Table

from ipod_cli import __version__
from ipod_cli.commands import export, playlists, tracks
from ipod_cli.device import discover_ipod_mounts, inspect_ipod, is_ipod_mount
from ipod_cli.ui import console, error_console, print_banner, print_error, print_info, print_success, print_warning

# Suprime logs de diagnóstico internos na visualização normal do terminal
logging.basicConfig(level=logging.ERROR, format="%(message)s")

app = typer.Typer(
    name="ipod",
    help="iPod Sync CLI: Gerencie e sincronize seu iPod (Classic, Video, Mini, Nano) no terminal.",
    add_completion=False,
    no_args_is_help=True,
)

# Registra os comandos
app.command(name="tracks")(tracks.list_tracks)
app.command(name="playlists")(playlists.list_playlists)
app.command(name="export")(export.export_tracks)




def version_callback(value: bool) -> None:
    if value:
        console.print(f"[bold cyan]ipod-sync[/bold cyan] versão [bold green]{__version__}[/bold green]")
        raise typer.Exit()


def _format_bytes(bytes_count: int) -> str:
    """Converte bytes para formato legível (KB, MB, GB)."""
    if bytes_count <= 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if bytes_count < 1024:
            return f"{bytes_count:.2f} {unit}"
        bytes_count /= 1024
    return f"{bytes_count:.2f} PB"


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-v",
        help="Exibe a versão do ipod-sync e encerra.",
        callback=version_callback,
        is_eager=True,
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Habilita logs de depuração detalhados.",
    ),
) -> None:
    """Ferramenta CLI moderna para gerenciar iPods sem iTunes."""
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)


@app.command(name="detect")
def detect_cmd() -> None:
    """Detecta automaticamente todos os iPods conectados ao computador."""
    print_banner()
    print_info("Procurando unidades e dispositivos iPod conectados...")

    mounts = discover_ipod_mounts()
    if not mounts:
        print_warning("Nenhum iPod detectado automaticamente.")
        console.print(
            "\n[dim]Dicas:\n"
            "1. Certifique-se de que o iPod está no Modo Disco (Disk Mode) ou montado no sistema operacional.\n"
            "2. Se souber a pasta ou letra de unidade, use: [bold]ipod info --mount <caminho>[/bold] (ex: [bold]ipod info -m E:\\\\[/bold])[/dim]\n"
        )
        return

    print_success(f"{len(mounts)} iPod(s) encontrado(s)!")

    table = Table(title="iPods Conectados", border_style="cyan")
    table.add_column("Ponto de Montagem", style="bold green")
    table.add_column("Modelo Identificado", style="bold white")
    table.add_column("Nome do iPod", style="cyan")
    table.add_column("Faixas", justify="right", style="magenta")
    table.add_column("Espaço Livre / Total", justify="right")

    for mount in mounts:
        try:
            device = inspect_ipod(mount)
            free_str = _format_bytes(device.free_bytes)
            total_str = _format_bytes(device.total_bytes)
            table.add_row(
                str(device.mount_point),
                device.model_name,
                device.device_name,
                str(device.track_count),
                f"{free_str} / {total_str}",
            )
        except Exception as e:
            table.add_row(str(mount), "[red]Erro ao inspecionar[/red]", "-", "-", str(e))

    console.print(table)


@app.command(name="info")
def info_cmd(
    mount_point: Optional[str] = typer.Option(
        None,
        "--mount",
        "-m",
        help="Letra da unidade ou caminho do iPod (ex: 'E:\\' ou '/media/ipod').",
    ),
) -> None:
    """Exibe informações completas de hardware, firmware e biblioteca do iPod."""
    print_banner()

    target_mount: Optional[Path] = None

    if mount_point:
        p = Path(mount_point)
        if not p.exists():
            print_error(f"O caminho especificado não existe: '{mount_point}'")
            raise typer.Exit(1)
        if not is_ipod_mount(p):
            print_warning(f"O diretório '{mount_point}' não parece ser um iPod (pasta 'iPod_Control' ausente).")
            if not typer.confirm("Deseja inspecionar mesmo assim?"):
                raise typer.Exit(1)
        target_mount = p
    else:
        mounts = discover_ipod_mounts()
        if not mounts:
            print_error("Nenhum iPod detectado automaticamente.")
            console.print("[dim]Use [bold]--mount <caminho>[/bold] para especificar a unidade do seu iPod manualmente.[/dim]")
            raise typer.Exit(1)
        target_mount = mounts[0]

    print_info(f"Inspecionando iPod em [bold]{target_mount}[/bold]...")

    try:
        dev = inspect_ipod(target_mount)
    except Exception as e:
        print_error(f"Falha ao inspecionar iPod: {e}")
        raise typer.Exit(1)

    # Tabela de Hardware & Identificação
    t_hw = Table(show_header=False, box=None, padding=(0, 2))
    t_hw.add_row("[bold cyan]Modelo:[/bold cyan]", f"[bold white]{dev.model_name}[/bold white]")
    t_hw.add_row("[bold cyan]Família / Geração:[/bold cyan]", f"{dev.family} {dev.generation}".strip() or "N/D")
    if dev.finish:
        t_hw.add_row("[bold cyan]Cor / Acabamento:[/bold cyan]", dev.finish)
    t_hw.add_row("[bold cyan]Número do Modelo:[/bold cyan]", dev.model_number or "N/D")
    t_hw.add_row("[bold cyan]Número de Série:[/bold cyan]", dev.serial_number or "N/D")
    t_hw.add_row("[bold cyan]Versão do Firmware:[/bold cyan]", dev.firmware_version or "N/D")

    # Armazenamento
    t_storage = Table(show_header=False, box=None, padding=(0, 2))
    used_pct = (dev.used_bytes / dev.total_bytes * 100) if dev.total_bytes > 0 else 0
    t_storage.add_row("[bold cyan]Capacidade Total:[/bold cyan]", _format_bytes(dev.total_bytes))
    t_storage.add_row("[bold cyan]Espaço Usado:[/bold cyan]", f"{_format_bytes(dev.used_bytes)} ({used_pct:.1f}%)")
    t_storage.add_row("[bold cyan]Espaço Livre:[/bold cyan]", _format_bytes(dev.free_bytes))

    # Recursos & Capacidades
    t_caps = Table(show_header=False, box=None, padding=(0, 2))
    t_caps.add_row("[bold cyan]Suporte ALAC (Lossless):[/bold cyan]", "[green]Sim[/green]" if dev.supports_alac else "[yellow]Não[/yellow]")
    t_caps.add_row("[bold cyan]Capas de Álbum (Artwork):[/bold cyan]", "[green]Sim[/green]" if dev.supports_artwork else "[yellow]Não[/yellow]")
    t_caps.add_row("[bold cyan]Reprodução de Vídeo:[/bold cyan]", "[green]Sim[/green]" if dev.supports_video else "[dim]Não[/dim]")

    # Biblioteca do iPod
    t_lib = Table(show_header=False, box=None, padding=(0, 2))
    t_lib.add_row("[bold cyan]Nome do Dispositivo:[/bold cyan]", dev.device_name)
    t_lib.add_row("[bold cyan]Banco de Dados:[/bold cyan]", "[green]iTunesDB Presente[/green]" if dev.database_found else "[yellow]Não encontrado[/yellow]")
    t_lib.add_row("[bold cyan]Total de Músicas:[/bold cyan]", str(dev.track_count))
    t_lib.add_row("[bold cyan]Total de Playlists:[/bold cyan]", str(dev.playlist_count))

    console.print(Panel(t_hw, title="[bold]Identificação do Dispositivo[/bold]", border_style="cyan"))
    console.print(Panel(t_storage, title="[bold]Armazenamento & Disco[/bold]", border_style="green"))
    console.print(Panel(t_caps, title="[bold]Recursos Suportados[/bold]", border_style="blue"))
    console.print(Panel(t_lib, title="[bold]Estatísticas da Biblioteca[/bold]", border_style="magenta"))


if __name__ == "__main__":
    app()
