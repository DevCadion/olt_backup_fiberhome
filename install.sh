#!/bin/bash
# Instalador / atualizador do Backup OLT FiberHome.
# Pode ser executado de novo com segurança (após upgrade do Debian, por exemplo):
# o venv novo é montado ao lado e só substitui o antigo se tudo der certo.
set -euo pipefail
umask 077

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'

# Sempre trabalhar no diretório do projeto, antes de qualquer remoção/criação
cd "$(dirname "$(readlink -f "$0")")"
APP_DIR=$(pwd)
INSTALL_LOG="$APP_DIR/install.log"
: > "$INSTALL_LOG"

die() { echo -e "${RED}ERRO: $*${NC}"; echo -e "Detalhes em: $INSTALL_LOG"; exit 1; }

echo -e "${CYAN}╔═════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║   ${YELLOW}BACKUP AUTOMÁTICO DE OLTs    ║         ${CYAN}║${NC}"
echo -e "${CYAN}║   ${YELLOW}FiberHome | Telnet/SSH | FTP ║         ${CYAN}║${NC}"
echo -e "${CYAN}║   ${YELLOW}CTLNET NOC                   ║         ${CYAN}║${NC}"
echo -e "${CYAN}╚═════════════════════════════════════════╝${NC}"

# 1. Dependências do sistema (somente se root; senão apenas verifica)
echo -e "${GREEN}[1/5] Dependências do sistema...${NC}"
if [ "$(id -u)" -eq 0 ]; then
    apt-get update >>"$INSTALL_LOG" 2>&1 || die "apt-get update falhou"
    # 'telnet' é pacote de transição para inetutils-telnet no Debian 13
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3 python3-venv telnet openssh-client >>"$INSTALL_LOG" 2>&1 \
        || die "falha ao instalar pacotes"
else
    echo -e "${YELLOW}Não é root: pulando apt. Garanta python3, python3-venv, telnet e openssh-client.${NC}"
fi
command -v python3 >/dev/null || die "python3 não encontrado"
command -v telnet  >/dev/null || echo -e "${YELLOW}Aviso: telnet não encontrado.${NC}"

# 2. Ambiente virtual (cria em venv.new e troca no final -> sem janela quebrada)
echo -e "${GREEN}[2/5] Criando ambiente virtual ($(python3 --version))...${NC}"
rm -rf venv.new
python3 -m venv venv.new >>"$INSTALL_LOG" 2>&1 || die "falha ao criar venv"

# 3. Dependências Python com versão fixa e verificação de hash
echo -e "${GREEN}[3/5] Instalando dependências Python (com verificação de hash)...${NC}"
./venv.new/bin/python3 -m pip install --upgrade pip >>"$INSTALL_LOG" 2>&1 || die "falha ao atualizar pip"
./venv.new/bin/python3 -m pip install --require-hashes --only-binary=:all: \
    -r requirements.txt >>"$INSTALL_LOG" 2>&1 || die "falha ao instalar requirements"
./venv.new/bin/python3 -c "import pexpect, dotenv" || die "módulos não importam no venv novo"

if [ -d venv ]; then rm -rf venv.old; mv venv venv.old; fi
mv venv.new venv
rm -rf venv.old
VENV_PYTHON="$APP_DIR/venv/bin/python3"
SCRIPT_PATH="$APP_DIR/backup_olt.py"

# 4. .env e permissões
echo -e "${GREEN}[4/5] Configurando .env e permissões...${NC}"
if [ ! -f .env ]; then
    cp .env.example .env
    echo -e "${YELLOW}Arquivo .env criado. EDITE-O COM SUAS CREDENCIAIS.${NC}"
else
    echo "Arquivo .env já existe. Mantido."
fi
chmod 600 .env
LOG_PATH="$APP_DIR/backup_olt.log"
touch "$LOG_PATH"; chmod 640 "$LOG_PATH"
chmod 700 "$APP_DIR"

if [ "$(id -u)" -eq 0 ] && [ -d /etc/logrotate.d ]; then
    cat > /etc/logrotate.d/backup_olt <<EOF
"$LOG_PATH" {
    weekly
    rotate 12
    compress
    missingok
    notifempty
    create 0640 $(stat -c %U "$APP_DIR") $(stat -c %G "$APP_DIR")
}
EOF
    chmod 644 /etc/logrotate.d/backup_olt
fi

# 5. Cron
echo -e "${GREEN}[5/5] Agendamento no cron...${NC}"
EXISTING=$(crontab -l 2>/dev/null | grep -F "$SCRIPT_PATH" || true)
if [ -n "$EXISTING" ] && [ "${RECONFIGURE_CRON:-0}" != "1" ]; then
    echo -e "Agendamento existente mantido: ${CYAN}$EXISTING${NC}"
    echo -e "(para mudar o horário: RECONFIGURE_CRON=1 ./install.sh)"
else
    read -r -p "Hora (0-23) [padrão 02]: " B_HOUR || true
    read -r -p "Minuto (0-59) [padrão 00]: " B_MIN || true
    B_HOUR=${B_HOUR:-2}; B_MIN=${B_MIN:-0}
    if ! [[ "$B_HOUR" =~ ^[0-9]{1,2}$ ]] || [ "$((10#$B_HOUR))" -gt 23 ]; then B_HOUR=2; fi
    if ! [[ "$B_MIN"  =~ ^[0-9]{1,2}$ ]] || [ "$((10#$B_MIN))"  -gt 59 ]; then B_MIN=0; fi
    B_HOUR=$((10#$B_HOUR)); B_MIN=$((10#$B_MIN))

    CRON_JOB="$B_MIN $B_HOUR * * * \"$VENV_PYTHON\" \"$SCRIPT_PATH\" >> \"$LOG_PATH\" 2>&1"
    TMP_CRON=$(mktemp)
    trap 'rm -f "$TMP_CRON"' EXIT
    crontab -l 2>/dev/null | grep -vF "$SCRIPT_PATH" > "$TMP_CRON" || true
    echo "$CRON_JOB" >> "$TMP_CRON"
    crontab "$TMP_CRON"
    echo -e "${CYAN}Backup agendado para ${YELLOW}$(printf '%02d:%02d' "$B_HOUR" "$B_MIN")${CYAN}.${NC}"
fi

echo -e "${GREEN}Instalação concluída.${NC}"
echo -e "Validar sem acessar as OLTs: ${GREEN}$VENV_PYTHON $SCRIPT_PATH --check${NC}"
echo -e "Rodar manualmente:          ${GREEN}$VENV_PYTHON $SCRIPT_PATH${NC}"
