#!/bin/bash

# Cores para saída
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Banner Personalizado
clear
echo -e "${CYAN}╔════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║   ${YELLOW}BACKUP AUTOMÁTICO DE OLTs             ${CYAN}║${NC}"
echo -e "${CYAN}║   ${YELLOW}FiberHome | Telnet | FTP              ${CYAN}║${NC}"
echo -e "${CYAN}║   ${YELLOW}CTLNET NOC                            ${CYAN}║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════╝${NC}"
echo ""

echo -e "${YELLOW}Iniciando instalação do Backup OLT Fiberhome...${NC}"

# 1. Verificar dependências do sistema
echo -e "${GREEN}[1/5] Verificando dependências do sistema...${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Python3 não encontrado. Por favor, instale o Python3.${NC}"
    exit 1
fi

# 2. Criar ambiente virtual
echo -e "${GREEN}[2/5] Criando ambiente virtual (venv)...${NC}"
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate

# 3. Instalar dependências Python
echo -e "${GREEN}[3/5] Instalando dependências Python...${NC}"
pip install --upgrade pip &> /dev/null
pip install -r requirements.txt &> /dev/null

# 4. Configurar arquivo .env
echo -e "${GREEN}[4/5] Configurando arquivo .env...${NC}"
if [ ! -f .env ]; then
    cp .env.example .env
    echo -e "${YELLOW}Arquivo .env criado. EDITE-O COM SUAS CREDENCIAIS.${NC}"
else
    echo -e "Arquivo .env já existe. Pulando cópia."
fi

# 5. Configurar Cron com Horário Escolhido
echo -e "${GREEN}[5/5] Configurando automação no Cron...${NC}"

echo -e "${YELLOW}Qual horário você deseja que o backup diário seja executado?${NC}"
read -p "Hora (0-23) [padrão 02]: " B_HOUR
read -p "Minuto (0-59) [padrão 00]: " B_MIN

# Valores padrão se vazio
B_HOUR=${B_HOUR:-02}
B_MIN=${B_MIN:-00}

# Validar se são números
if ! [[ "$B_HOUR" =~ ^[0-9]+$ ]] || [ "$B_HOUR" -gt 23 ]; then B_HOUR=02; fi
if ! [[ "$B_MIN" =~ ^[0-9]+$ ]] || [ "$B_MIN" -gt 59 ]; then B_MIN=00; fi

# Garante que estamos no diretório correto
cd "$(dirname "$0")"
SCRIPT_PATH=$(pwd)/backup_olt.py
VENV_PYTHON=$(pwd)/venv/bin/python3

# Validação de segurança: verificar se os arquivos existem
if [ ! -f "$SCRIPT_PATH" ]; then
    echo -e "${RED}ERRO: O arquivo $SCRIPT_PATH não foi encontrado!${NC}"
    exit 1
fi

if [ ! -f "$VENV_PYTHON" ]; then
    echo -e "${RED}ERRO: O ambiente virtual não foi criado corretamente!${NC}"
    exit 1
fi
LOG_PATH=$(pwd)/backup_olt.log
touch $LOG_PATH
chmod 666 $LOG_PATH
CRON_JOB="$B_MIN $B_HOUR * * * cd \"$(pwd)\" && \"$VENV_PYTHON\" \"$SCRIPT_PATH\" >> \"$LOG_PATH\" 2>&1"

# Remover agendamentos antigos deste script para evitar duplicidade
crontab -l 2>/dev/null | grep -v "$SCRIPT_PATH" > mycron
# Adicionar o novo agendamento
echo "$CRON_JOB" >> mycron
crontab mycron
rm mycron

echo -e "${GREEN}Instalação concluída com sucesso!${NC}"
echo -e "${CYAN}O backup foi agendado para rodar todos os dias às ${YELLOW}${B_HOUR}:${B_MIN}${CYAN}.${NC}"
echo -e "${YELLOW}Lembre-se de configurar as OLTs no arquivo .env.${NC}"
echo -e "Para rodar manualmente: ${GREEN}./venv/bin/python3 backup_olt.py${NC}"