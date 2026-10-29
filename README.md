# ipod-sync

CLI moderna, de alto desempenho e multiplataforma para gerenciar e sincronizar músicas no seu iPod (Classic, Video 5th/5.5th, Mini, Nano) sem precisar do iTunes.

Baseado na engenharia de baixo nível do [iOpenPod](https://github.com/TheRealSavi/iOpenPod) — com parsing nativo de `iTunesDB`, `ArtworkDB` e transações atômicas de disco com journal, mas **100% headless** (sem interface gráfica ou dependências de Qt).

---

## ⚡ Início Rápido (Quickstart com `uv`)

Não é necessário configurar ambientes virtuais manualmente se você tiver o [`uv`](https://docs.astral.sh/uv/) instalado.

```bash
# 1. Clonar o repositório
git clone https://github.com/<seu-usuario>/ipod-sync.git
cd ipod-sync

# 2. Executar comandos diretamente
uv run ipod --help
uv run ipod --version
```

### Instalação Tradicional via `pip`

```bash
pip install -e .
ipod --help
```

---

## 🚀 Comandos

| Comando | Descrição |
| :--- | :--- |
| `ipod detect` | Procura automaticamente unidades e volumes de iPods conectados |
| `ipod info` | Exibe especificações de hardware, geração, firmware e estatísticas |
| `ipod tracks list` | Lista as faixas na biblioteca com filtros (`--artist`, `--album`) |
| `ipod add <arquivos>` | Converte (se necessário para ALAC 44.1kHz), gera capa e adiciona faixas |
| `ipod sync <pasta>` | Sincroniza uma pasta local de músicas com o iPod (com suporte a dry-run) |
| `ipod export <pasta>` | Extrai as músicas do iPod recuperando nomes e estrutura de pastas original |
| `ipod backup` | Cria backup rápido do `iTunesDB` e arquivos de sistema |

---

## 🎵 Perfil de Alta Fidelidade (iPod 5.5 / Video)

Para o iPod Video 5.5th Gen (DAC Wolfson WM8758), a ferramenta inclui suporte nativo ao pipeline de conversão:
* **Codec**: ALAC (`-c:a alac`) em 16-bit (`-sample_fmt s16p`).
* **Taxa**: 44.100 Hz com resampler **SoX** de alta precisão (`aresample=resampler=soxr`).
* **Metadados**: Preservação total de tags e capa embutida no container M4A (`attached_pic`).
* **Streaming**: Otimização `-movflags +faststart` para leitura instantânea no disco.

---

## 📜 Licença

GPL-3.0-or-later — consulte o arquivo [LICENSE](LICENSE) para detalhes.
