import pexpect
import datetime
import os
import smtplib
import socket
from email.mime.text import MIMEText
from dotenv import load_dotenv

# =========================
# LOAD ENV
# =========================
load_dotenv()

# =========================
# CONFIGURAÇÕES GERAIS
# =========================
TIMEOUT = 14

# =========================
# FTP
# =========================
FTP_IP = os.getenv("FTP_IP")
FTP_USER = os.getenv("FTP_USER")
FTP_PASS = os.getenv("FTP_PASS")

# =========================
# EMAIL (SMTP SIMPLES)
# =========================
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", "25"))
SMTP_USER = os.getenv("SMTP_USER") or "backup-olt@localhost"
SMTP_PASS = os.getenv("SMTP_PASS") or None
EMAIL_TO = os.getenv("EMAIL_TO")

# =========================
# FUNÇÕES AUXILIARES
# =========================
def log(msg):
    now = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    print(f"[{now}] {msg}")

def load_olt_list():
    olt_list_str = os.getenv("OLT_LIST", "")
    if not olt_list_str:
        return []
    return [name.strip() for name in olt_list_str.split(",") if name.strip()]

def load_olt_credentials(name):
    return {
        "name": name,
        "ip": os.getenv(f"{name}_IP"),
        "user": os.getenv(f"{name}_USER"),
        "password": os.getenv(f"{name}_PASS"),
        "enable": os.getenv(f"{name}_ENABLE_PASS"),
    }

def parse_pexpect_error(err: str) -> str:
    if "Não há rota para o host" in err or "No route to host" in err:
        return "Sem rota para o host (problema de rede)"
    if "Connection refused" in err:
        return "Conexão recusada pela OLT"
    if "Timeout exceeded" in err:
        return "Timeout aguardando resposta da OLT"
    if "EOF" in err:
        return "Conexão encerrada inesperadamente"
    return "Falha inesperada durante a execução"

# =========================
# EMAIL
# =========================
def send_email(subject: str, body: str):
    if not EMAIL_TO or not SMTP_SERVER:
        log("⚠️ Configurações de e-mail incompletas. Pulando envio.")
        return

    try:
        recipients = [e.strip() for e in EMAIL_TO.split(",")]

        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = SMTP_USER
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=20) as server:
            server.ehlo()
            if SMTP_PORT == 587:
                server.starttls()
            if SMTP_PASS:
                server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(msg["From"], recipients, msg.as_string())

    except Exception as e:
        log(f"⚠️ Erro ao enviar e-mail: {e}")

def send_alert(olt, stage, error):
    hostname = socket.gethostname()
    now = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    reason = parse_pexpect_error(error)
    last_line = error.splitlines()[-1] if error else "N/A"

    body = f"""
🚨 ALERTA DE FALHA – BACKUP DE OLT 🚨

Resumo:
A rotina automática de backup encontrou uma falha.

Detalhes da OLT:
- Nome da OLT : {olt['name']}
- Endereço IP : {olt['ip']}

Ambiente:
- Servidor executor : {hostname}
- Data/Hora         : {now}

Etapa da falha:
- {stage}

Motivo identificado:
- {reason}

Mensagem técnica resumida:
- {last_line}

Ação recomendada:
- Verificar conectividade Telnet
- Conferir credenciais da OLT
- Validar acesso FTP
"""
    subject = f"[BACKUP OLT] FALHA em {olt['name']} ({olt['ip']})"
    send_email(subject, body)

def send_summary_email(success_list, fail_list):
    hostname = socket.gethostname()
    now = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    total = len(success_list) + len(fail_list)

    status_msg = "✅ SUCESSO TOTAL" if not fail_list else "⚠️ CONCLUÍDO COM FALHAS"
    
    success_names = ", ".join(success_list) if success_list else "Nenhuma"
    fail_names = ", ".join(fail_list) if fail_list else "Nenhuma"

    body = f"""
{status_msg} – BACKUP DE OLTs FINALIZADO

Resumo:
- Total processado : {total}
- Sucesso         : {len(success_list)}
- Falhas          : {len(fail_list)}

OLTs com Sucesso:
{success_names}

OLTs com Falha:
{fail_names}

Ambiente:
- Servidor executor : {hostname}
- Data/Hora         : {now}

Este e-mail é gerado automaticamente pelo sistema de backup.
"""
    subject = f"[BACKUP OLT] {status_msg} – {len(success_list)}/{total} OLTs"
    send_email(subject, body)

# =========================
# BACKUP OLT
# =========================
def backup_olt(olt):
    log(f"==== Iniciando backup {olt['name']} ({olt['ip']}) ====")
    
    if not all([olt['ip'], olt['user'], olt['password'], olt['enable']]):
        log(f"❌ ERRO: Credenciais incompletas para {olt['name']}")
        return False

    try:
        child = pexpect.spawn(
            f"telnet {olt['ip']}",
            encoding="utf-8",
            timeout=TIMEOUT
        )

        child.expect(["Username:", "Login:"])
        log("Prompt de usuário recebido")
        child.sendline(olt["user"])

        child.expect("Password:")
        log("Prompt de senha recebido")
        child.sendline(olt["password"])

        child.expect([">", "#"])
        log("Prompt operacional recebido")

        child.sendline("enable")
        child.expect("Password:")
        log("Prompt de senha de enable recebido")
        child.sendline(olt["enable"])

        child.expect("#")
        log("Modo enable confirmado")

        date = datetime.datetime.now().strftime("%d-%m-%Y")
        filename = f"backup{olt['name']}-{date}"

        cmd = f"upload ftp config {FTP_IP} {FTP_USER} {FTP_PASS} {filename}"
        log(f"Executando comando: {cmd}")
        child.sendline(cmd)

        # Timeout maior para o upload
        child.expect("#", timeout=120)
        log("Upload FTP finalizado")

        child.sendline("exit")
        child.close()

        log(f"✅ Backup realizado com sucesso: {olt['name']}")
        return True

    except Exception as e:
        log(f"❌ ERRO no backup {olt['name']}: {e}")
        send_alert(olt, "Conexão / Autenticação / Upload", str(e))
        return False

# =========================
# MAIN
# =========================
def main():
    olt_names = load_olt_list()
    if not olt_names:
        log("Nenhuma OLT configurada em OLT_LIST no arquivo .env")
        return

    success_list = []
    fail_list = []

    for name in olt_names:
        olt = load_olt_credentials(name)
        if backup_olt(olt):
            success_list.append(name)
        else:
            fail_list.append(name)

    send_summary_email(success_list, fail_list)

if __name__ == "__main__":
    main()
