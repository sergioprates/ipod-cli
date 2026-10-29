# ipod-sync

CLI moderna, de alta fidelidade e 100% headless para gerenciar e sincronizar músicas no seu iPod (Classic, Video 5th/5.5th, Mini, Nano) no terminal, sem precisar do iTunes.

Baseado na engenharia de baixo nível do [iOpenPod](https://github.com/TheRealSavi/iOpenPod) — com parsing e escrita nativa de `iTunesDB`, `ArtworkDB` e integridade transacional de disco, mas sem dependências gráficas pesadas (PySide6/Qt removidos).

---

## ⚡ Guia Rápido (Quickstart com `uv`)

O projeto foi estruturado para ser imediatamente utilizável por qualquer usuário ou agente (ex: Hermes) sem precisar configurar Python ou ambientes virtuais manualmente:

```bash
# 1. Clonar o repositório
git clone https://github.com/sergioprates/ipod-cli.git
cd ipod-cli

# 2. Executar comandos diretamente com uv
uv run ipod detect
uv run ipod info
uv run ipod tracks
```

*(Ou usando instalação tradicional com `pip install -e .`)*

---

## 🎧 Perfil Audiófilo (iPod 5.5th Gen / Wolfson WM8758 DAC)

Para o iPod Video 5.5th Gen (equipado com o lendário conversor analógico-digital Wolfson WM8758), o `ipod-sync` implementa transcodificação de máxima fidelidade via FFmpeg:

* **Codec**: Apple Lossless (`-c:a alac`) em 16-bit PCM (`-sample_fmt s16p`).
* **Taxa de Amostragem**: 44.100 Hz com resampler de altíssima precisão **SoX** (`aresample=resampler=soxr`) e fallback automático para SWR de alta qualidade.
* **Metadados e Capas**: Preservação integral de tags ID3/MP4 e capa de álbum no container M4A (`-c:v copy -disposition:v attached_pic`).
* **Disco & Buffer**: Flag `-movflags +faststart` para mover o átomo `moov` para o início do arquivo, eliminando atrasos no giro do disco do iPod.

Formatos como **FLAC**, **OGG**, **OPUS**, **WMA**, **APE** ou arquivos com taxa > 44.1kHz são automaticamente detectados e transcodificados antes do envio.

---

## 🛠️ Referência de Comandos

### 1. `ipod detect`
Varre as portas USB e unidades montadas, identificando iPods conectados com 100% de precisão via SCSI VPD e verificação de hardware.
```bash
uv run ipod detect
```

### 2. `ipod info`
Exibe relatório detalhado de hardware, modelo exato (ex: iPod Video 30GB Preto MA446), número de série, versão de firmware, espaço em disco e estatísticas da biblioteca.
```bash
uv run ipod info
uv run ipod info -m D:\
```

### 3. `ipod tracks`
Lista as faixas do iPod no terminal em formato de tabela estilizada ou JSON.
```bash
# Listar as primeiras 20 faixas
uv run ipod tracks --limit 20

# Filtrar por artista ou álbum
uv run ipod tracks --artist "Ariana Grande"
uv run ipod tracks --album "thank u, next"

# Busca textual geral
uv run ipod tracks --search "love"

# Exportar lista em JSON
uv run ipod tracks --json > faixas.json
```

### 4. `ipod playlists`
Lista todas as playlists configuradas na biblioteca do iPod.
```bash
uv run ipod playlists
```

### 5. `ipod export`
Extrai as músicas do iPod recuperando seus nomes originais e organizando em pastas `Artista/Álbum/01 - Faixa.ext`.
```bash
# Simulação prévia
uv run ipod export ./musicas_recuperadas --artist "Ariana Grande" --dry-run

# Extração real
uv run ipod export ./musicas_recuperadas --artist "Ariana Grande"
```

### 6. `ipod add`
Adiciona faixas ou pastas inteiras de música ao iPod, com transcodificação automática se necessário.
```bash
# Adicionar arquivo individual (com simulação prévia)
uv run ipod add ./musicas/album/faixa.flac --dry-run

# Adicionar pasta inteira convertendo tudo para ALAC 44.1kHz
uv run ipod add ./minha_colecao --force-alac

# Adicionar arquivos nativos (MP3, M4A, etc.)
uv run ipod add ./novas_musicas/
```

### 7. `ipod remove`
Remove músicas da biblioteca do iPod e apaga seus arquivos físicos das pastas internas `Fxx/`.
```bash
# Remover por ID
uv run ipod remove 780 781

# Remover por busca textual (com confirmação)
uv run ipod remove --search "Artista Desconhecido"

# Simular remoção sem apagar nada
uv run ipod remove --search "Album Antigo" --dry-run
```

### 8. `ipod sync`
Sincroniza uma pasta local de músicas com o iPod, adicionando faixas novas e opcionalmente removendo as que foram excluídas localmente.
```bash
# Simulação prévia do plano de sincronização
uv run ipod sync "C:\Users\Usuario\Music" --dry-run

# Sincronização real
uv run ipod sync "C:\Users\Usuario\Music"

# Sincronização espelhada (remove do iPod o que não existe localmente)
uv run ipod sync "C:\Users\Usuario\Music" --delete
```

### 9. `ipod backup`
Gerencia cópias de segurança do banco de dados `iTunesDB` e arquivos do sistema do iPod.
```bash
# Criar backup completo
uv run ipod backup create

# Listar backups existentes
uv run ipod backup list

# Restaurar um backup
uv run ipod backup restore "ipod_backups/iPod_backup_20261029.zip"
```

---

## 🔒 Segurança de Dados & Transações Atômicas

* Toda operação de escrita (`add`, `remove`, `sync`) gera automaticamente uma cópia de segurança (`iTunesDB.bak` no iPod e arquivo versionado em `ipod_backups/pre_sync/`).
* A substituição do banco de dados é feita de forma atômica via arquivo temporário `iTunesDB.tmp`, garantindo que um desligamento inesperado nunca corrompa o disco do iPod.

---

## 📜 Licença

GPL-3.0-or-later — consulte o arquivo [LICENSE](LICENSE) para detalhes completos.
