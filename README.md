# Backup OLT Fiberhome (Escalável)

Automatiza o backup de múltiplas OLTs Fiberhome. O script acessa cada OLT por Telnet (ou SSH, se o firmware suportar), executa `upload ftp config`, **confere no FTP se o arquivo chegou**, remove os backups antigos e envia um resumo por e-mail.

Roda no Debian 13 com Python 3.13 e também é compatível com Debian 12 (Python 3.11).

## Estrutura

- `backup_olt.py`: script principal.
- `install.sh`: instala ou atualiza o venv (com verificação de hash), ajusta permissões, logrotate e cron. Pode ser rodado de novo com segurança.
- `.env`: configuração e senhas, gerado a partir do `.env.example` (deve ficar com `chmod 600`).
- `requirements.txt`: dependências com versão fixa e hash SHA-256.

## Instalação

```bash
chmod +x install.sh && sudo ./install.sh
```
```bash
sudo nano .env
```
```bash
sudo ./venv/bin/python3 backup_olt.py --check
```

O `--check` valida a configuração e testa o FTP e o SMTP sem acessar as OLTs.

## Múltiplas OLTs

```env
OLT_LIST=OLT_SUL,OLT_NORTE

OLT_SUL_IP=10.1.1.1
OLT_SUL_USER=admin
OLT_SUL_PASS=senha123
OLT_SUL_ENABLE_PASS=enable123
OLT_SUL_PROTO=telnet      # ou ssh
```

Os nomes das OLTs aceitam só letras, números e `_`. `FTP_USER` e `FTP_PASS` não podem ter espaço nem aspas, porque vão como argumentos na CLI da OLT.

## Segurança

- As senhas nunca aparecem no log nem no e-mail: elas são substituídas por `********`.
- A senha SMTP só é enviada com TLS (STARTTLS ou SSL).
- A limpeza só roda depois que o backup novo é confirmado no FTP, e só apaga arquivos da própria OLT.
- Um lock impede duas execuções ao mesmo tempo.
- O código de saída é diferente de zero quando alguma OLT falha, o que ajuda no monitoramento.
- Telnet e FTP são texto claro, uma limitação da OLT. Restrinja o acesso por firewall e rede de gerência: libere o FTP só para os IPs das OLTs e use um usuário FTP exclusivo, com chroot e sem shell.

## Agendamento e logs

O `install.sh` cria um agendamento diário no cron (02:00 por padrão). Para mudar o horário: `RECONFIGURE_CRON=1 ./install.sh`. Para ver o agendamento: `crontab -l`.

Log: `backup_olt.log`, com rotação semanal pelo logrotate.
