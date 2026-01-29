# Backup OLT Fiberhome (Escalável)

Este projeto automatiza o backup de múltiplas OLTs Fiberhome via Telnet e realiza o upload das configurações para um servidor FTP, este serviço roda no debian 13 com python3.13.

## Estrutura do Projeto

- `backup_olt.py`: Script principal em Python.
- `install.sh`: Script de instalação automatizada e configuração do Cron.
- `.env`: Arquivo de configuração (gerado a partir do `.env.example`).
- `requirements.txt`: Dependências do projeto.

## Como Instalar

1. Dê permissão de execução ao instalador:
   ```bash
   chmod +x install.sh
   ```

2. Execute o instalador:
   ```bash
   ./install.sh
   ```

3. Edite o arquivo `.env` com suas informações:
   ```bash
   nano .env
   ```

## Configuração de Múltiplas OLTs

No arquivo `.env`, defina a lista de OLTs separadas por vírgula e as credenciais para cada uma:

```env
OLT_LIST=OLT_SUL,OLT_NORTE

OLT_SUL_IP=10.1.1.1
OLT_SUL_USER=admin
OLT_SUL_PASS=senha123
OLT_SUL_ENABLE_PASS=enable123

OLT_NORTE_IP=10.1.1.2
OLT_NORTE_USER=admin
OLT_NORTE_PASS=senha456
OLT_NORTE_ENABLE_PASS=enable456
```

## Automação

O script de instalação configura automaticamente um agendamento no `cron` para rodar o backup todos os dias às **02:00 AM** ou o horário de sua preferencia..

Para verificar o agendamento:
```bash
crontab -l
```

## Logs

Acompanhe a execução através do arquivo `backup_olt.log`.
