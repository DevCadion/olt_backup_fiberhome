#!/usr/bin/env python3
"""
Backup automático de OLTs FiberHome.

Fluxo: acessa a OLT (Telnet ou SSH) -> executa `upload ftp config` -> confere
no servidor FTP se o arquivo chegou -> remove backups antigos -> envia e-mail.

Uso:
    backup_olt.py            executa o backup de todas as OLTs do OLT_LIST
    backup_olt.py --check    só valida configuração, FTP e SMTP (não acessa OLT)
"""
import argparse
import datetime
import fcntl
import ftplib
import ipaddress
import os
import re
import smtplib
import socket
import ssl
import stat
import sys
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import pexpect
from dotenv import load_dotenv

# =========================
# LOAD ENV (sempre o .env ao lado do script, independente do cwd do cron)
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
LOCK_PATH = os.path.join(BASE_DIR, ".backup_olt.lock")
KNOWN_HOSTS = os.path.join(BASE_DIR, ".ssh_known_hosts")
load_dotenv(ENV_PATH, override=False)


def env_bool(name, default=False):
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "sim", "on")


def env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# =========================
# CONFIGURAÇÕES GERAIS
# =========================
TIMEOUT = env_int("OLT_TIMEOUT", 14)
UPLOAD_TIMEOUT = env_int("OLT_UPLOAD_TIMEOUT", 120)
KEEP_BACKUPS = env_int("KEEP_BACKUPS", 2)

# =========================
# FTP
# =========================
FTP_IP = os.getenv("FTP_IP")
FTP_PORT = env_int("FTP_PORT", 21)
FTP_USER = os.getenv("FTP_USER")
FTP_PASS = os.getenv("FTP_PASS")
# FTPS (TLS explícito) apenas para a conexão deste script (verificação/limpeza).
# A OLT continua enviando em FTP simples, pois o firmware não suporta TLS.
FTP_TLS = env_bool("FTP_TLS", False)
# Diretório remoto onde a OLT grava (vazio = diretório inicial do usuário FTP)
FTP_DIR = os.getenv("FTP_DIR", "").strip()

# =========================
# EMAIL
# =========================
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = env_int("SMTP_PORT", 25)
SMTP_USER = os.getenv("SMTP_USER") or ""
SMTP_PASS = os.getenv("SMTP_PASS") or None
SMTP_FROM = os.getenv("SMTP_FROM") or SMTP_USER or "backup-olt@localhost"
EMAIL_TO = os.getenv("EMAIL_TO")
# none | starttls | ssl  (padrão: ssl na 465, starttls na 587, none nas demais)
SMTP_SECURITY = (os.getenv("SMTP_SECURITY") or "").strip().lower()

NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")
# Usuário/senha vão como argumentos de um comando da CLI da OLT: não podem ter
# espaço, aspas ou caracteres de controle, senão o comando quebra/é injetável.
CLI_SAFE_RE = re.compile(r"^[A-Za-z0-9_.@%+=:,/!#$*^~-]{1,64}$")


# =========================
# REDAÇÃO DE SEGREDOS
# =========================
def _collect_secrets():
    secrets = {FTP_PASS, SMTP_PASS}
    for name in load_olt_list():
        secrets.add(os.getenv(f"{name}_PASS"))
        secrets.add(os.getenv(f"{name}_ENABLE_PASS"))
    return sorted((s for s in secrets if s and len(s) >= 3), key=len, reverse=True)


def redact(text) -> str:
    """Remove qualquer senha conhecida de um texto antes de logar ou enviar."""
    text = "" if text is None else str(text)
    for secret in _SECRETS:
        text = text.replace(secret, "********")
    return text


# =========================
# FUNÇÕES AUXILIARES
# =========================
def log(msg):
    now = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    print(f"[{now}] {redact(msg)}", flush=True)


def load_olt_list():
    olt_list_str = os.getenv("OLT_LIST", "")
    return [n.strip() for n in olt_list_str.split(",") if n.strip()]


_SECRETS = []  # preenchido em main(), depois que o .env foi carregado


def load_olt_credentials(name):
    return {
        "name": name,
        "ip": os.getenv(f"{name}_IP"),
        "user": os.getenv(f"{name}_USER"),
        "password": os.getenv(f"{name}_PASS"),
        "enable": os.getenv(f"{name}_ENABLE_PASS"),
        "proto": (os.getenv(f"{name}_PROTO") or "telnet").strip().lower(),
        "port": os.getenv(f"{name}_PORT"),
    }


def valid_ip(value) -> bool:
    try:
        ipaddress.ip_address((value or "").strip())
        return True
    except ValueError:
        return False


def check_env_permissions():
    """Avisa se o .env (que guarda todas as senhas) estiver legível por outros."""
    try:
        mode = os.stat(ENV_PATH).st_mode
    except FileNotFoundError:
        log(f"⚠️ Arquivo {ENV_PATH} não encontrado")
        return
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        log(f"⚠️ SEGURANÇA: {ENV_PATH} está acessível a outros usuários "
            f"(modo {oct(mode & 0o777)}). Corrija com: chmod 600 {ENV_PATH}")


def validate_global_config():
    errors = []
    if not valid_ip(FTP_IP):
        errors.append("FTP_IP ausente ou inválido")
    if not FTP_USER or not CLI_SAFE_RE.match(FTP_USER):
        errors.append("FTP_USER ausente ou com caracteres não permitidos (espaço/aspas)")
    if not FTP_PASS or not CLI_SAFE_RE.match(FTP_PASS):
        errors.append("FTP_PASS ausente ou com caracteres não permitidos (espaço/aspas)")
    if KEEP_BACKUPS < 1:
        errors.append("KEEP_BACKUPS deve ser >= 1")
    return errors


def validate_olt(olt):
    errors = []
    if not NAME_RE.match(olt["name"]):
        errors.append("nome inválido (use apenas letras, números e _)")
    if not valid_ip(olt["ip"]):
        errors.append("IP ausente ou inválido")
    if not all([olt["user"], olt["password"], olt["enable"]]):
        errors.append("credenciais incompletas")
    if olt["proto"] not in ("telnet", "ssh"):
        errors.append("PROTO deve ser telnet ou ssh")
    if olt["port"] and not olt["port"].isdigit():
        errors.append("PORT inválida")
    return errors


def parse_pexpect_error(err: str) -> str:
    if "Não há rota para o host" in err or "No route to host" in err:
        return "Sem rota para o host (problema de rede)"
    if "Connection refused" in err:
        return "Conexão recusada pela OLT"
    if "Timeout exceeded" in err or "TIMEOUT" in err:
        return "Timeout aguardando resposta da OLT"
    if "Host key verification failed" in err or "REMOTE HOST IDENTIFICATION" in err:
        return "Chave SSH da OLT mudou (possível troca de equipamento ou ataque MITM)"
    if "EOF" in err:
        return "Conexão encerrada inesperadamente"
    return "Falha inesperada durante a execução"


def backup_filename(olt_name, date=None):
    date = date or datetime.datetime.now()
    return f"backup{olt_name}-{date.strftime('%d-%m-%Y')}"


# =========================
# FTP (verificação e garbage collector)
# =========================
def ftp_connect():
    if FTP_TLS:
        ctx = ssl.create_default_context()
        ftp = ftplib.FTP_TLS(context=ctx, timeout=20)
        ftp.connect(FTP_IP, FTP_PORT)
        ftp.login(FTP_USER, FTP_PASS)  # FTP_TLS faz AUTH TLS antes do login
        ftp.prot_p()                   # canal de dados também criptografado
    else:
        ftp = ftplib.FTP(timeout=20)
        ftp.connect(FTP_IP, FTP_PORT)
        ftp.login(FTP_USER, FTP_PASS)
    if FTP_DIR:
        ftp.cwd(FTP_DIR)
    return ftp


def verify_upload(filename) -> bool:
    """Confirma que o arquivo existe no FTP e não está vazio."""
    try:
        ftp = ftp_connect()
        try:
            files = {os.path.basename(f) for f in ftp.nlst()}
            if filename not in files:
                log(f"❌ Arquivo {filename} não encontrado no FTP após o upload")
                return False
            try:
                ftp.voidcmd("TYPE I")
                size = ftp.size(filename)
            except ftplib.error_perm:
                size = None  # servidor não suporta SIZE: aceita a existência
            if size is not None and size <= 0:
                log(f"❌ Arquivo {filename} está vazio no FTP")
                return False
            log(f"✔️ Arquivo confirmado no FTP: {filename}"
                + (f" ({size} bytes)" if size is not None else ""))
            return True
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()
    except Exception as e:
        log(f"⚠️ Não foi possível verificar o upload no FTP: {e}")
        return False


def cleanup_old_backups(olt_name, keep=KEEP_BACKUPS):
    log(f"🧹 Iniciando limpeza de backups antigos para {olt_name}")
    # Casa só os arquivos DESTA OLT (evita que "OLT" apague os de "OLT_SUL")
    pattern = re.compile(rf"^backup{re.escape(olt_name)}-(\d{{2}}-\d{{2}}-\d{{4}})$")

    try:
        ftp = ftp_connect()
        try:
            dated = []
            for f in ftp.nlst():
                m = pattern.match(os.path.basename(f))
                if not m:
                    continue
                try:
                    dated.append((datetime.datetime.strptime(m.group(1), "%d-%m-%Y"), f))
                except ValueError:
                    continue  # nome com data inválida: não mexe

            if len(dated) <= keep:
                log(f"✔️ Nada para limpar em {olt_name}")
                return

            dated.sort(reverse=True)
            for _, file in dated[keep:]:
                log(f"🗑 Removendo backup antigo: {file}")
                ftp.delete(file)
            log(f"✅ Limpeza concluída para {olt_name}")
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()
    except Exception as e:
        log(f"⚠️ Erro ao limpar backups de {olt_name}: {e}")


# =========================
# EMAIL
# =========================
def _smtp_security():
    if SMTP_SECURITY in ("none", "starttls", "ssl"):
        return SMTP_SECURITY
    if SMTP_PORT == 465:
        return "ssl"
    if SMTP_PORT == 587:
        return "starttls"
    return "none"


def smtp_open():
    security = _smtp_security()
    ctx = ssl.create_default_context()
    if security == "ssl":
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=20, context=ctx)
        server.ehlo()
    else:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=20)
        server.ehlo()
        if security == "starttls":
            server.starttls(context=ctx)
            server.ehlo()
    if SMTP_PASS:
        if security == "none":
            # Nunca manda senha SMTP em texto claro.
            server.quit()
            raise RuntimeError("SMTP_PASS definido sem TLS. Use SMTP_SECURITY=starttls/ssl "
                               "ou remova SMTP_PASS para relay sem autenticação.")
        server.login(SMTP_USER, SMTP_PASS)
    return server


def send_email(subject: str, body: str):
    if not EMAIL_TO or not SMTP_SERVER:
        log("⚠️ Configurações de e-mail incompletas. Pulando envio.")
        return

    try:
        recipients = [e.strip() for e in EMAIL_TO.split(",") if e.strip()]
        msg = EmailMessage()
        msg["From"] = SMTP_FROM
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = redact(subject)
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=socket.getfqdn() or "localhost")
        msg.set_content(redact(body), charset="utf-8")

        with smtp_open() as server:
            server.send_message(msg, from_addr=SMTP_FROM, to_addrs=recipients)
    except Exception as e:
        log(f"⚠️ Erro ao enviar e-mail: {e}")


def send_alert(olt, stage, error):
    hostname = socket.gethostname()
    now = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    error = redact(error)
    reason = parse_pexpect_error(error)
    # A linha "before" do pexpect pode conter o eco do comando; já foi redigida.
    lines = [l for l in error.splitlines() if l.strip()]
    last_line = lines[-1][:300] if lines else "N/A"

    body = f"""
🚨 ALERTA DE FALHA – BACKUP DE OLT 🚨

Resumo:
A rotina automática de backup encontrou uma falha.

Detalhes da OLT:
- Nome da OLT : {olt['name']}
- Endereço IP : {olt['ip']}
- Protocolo   : {olt['proto']}

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
- Verificar conectividade {olt['proto'].upper()}
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
UPLOAD_ERROR_RE = re.compile(r"(fail|error|denied|timeout|unreachable|not\s+exist)", re.I)


def spawn_session(olt):
    """Abre a sessão sem shell: argumentos em lista, sem interpolação de string."""
    ip = olt["ip"].strip()
    if olt["proto"] == "ssh":
        args = [
            "-o", "StrictHostKeyChecking=accept-new",   # TOFU: grava na 1ª vez, bloqueia se mudar
            "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
            "-o", "PreferredAuthentications=password,keyboard-interactive",
            "-o", "PubkeyAuthentication=no",
            "-o", f"ConnectTimeout={TIMEOUT}",
            "-p", olt["port"] or "22",
            "-l", olt["user"],
            ip,
        ]
        extra = os.getenv(f"{olt['name']}_SSH_OPTS", "").split()
        return pexpect.spawn("ssh", extra + args, encoding="utf-8", timeout=TIMEOUT)

    args = [ip] + ([olt["port"]] if olt["port"] else [])
    return pexpect.spawn("telnet", args, encoding="utf-8", timeout=TIMEOUT)


def backup_olt(olt):
    log(f"==== Iniciando backup {olt['name']} ({olt['ip']}) via {olt['proto']} ====")

    problems = validate_olt(olt)
    if problems:
        msg = "; ".join(problems)
        log(f"❌ ERRO de configuração em {olt['name']}: {msg}")
        send_alert(olt, "Validação da configuração", msg)
        return False

    child = None
    stage = "Conexão"
    try:
        child = spawn_session(olt)

        if olt["proto"] == "telnet":
            child.expect(["Username:", "Login:"])
            log("Prompt de usuário recebido")
            child.sendline(olt["user"])

        stage = "Autenticação"
        child.expect(r"[Pp]assword:")
        log("Prompt de senha recebido")
        child.sendline(olt["password"])

        child.expect([">", "#"])
        log("Prompt operacional recebido")

        stage = "Enable"
        child.sendline("enable")
        child.expect(r"[Pp]assword:")
        log("Prompt de senha de enable recebido")
        child.sendline(olt["enable"])

        child.expect("#")
        log("Modo enable confirmado")

        stage = "Upload FTP"
        filename = backup_filename(olt["name"])
        cmd = f"upload ftp config {FTP_IP.strip()} {FTP_USER} {FTP_PASS} {filename}"
        log(f"Executando comando: upload ftp config {FTP_IP} {FTP_USER} ******** {filename}")
        child.sendline(cmd)

        child.expect("#", timeout=UPLOAD_TIMEOUT)
        output = redact(child.before)
        log("Upload FTP finalizado na OLT")
        if UPLOAD_ERROR_RE.search(output):
            log(f"⚠️ Saída da OLT indica possível erro: {' '.join(output.split())[-200:]}")

        try:
            child.sendline("exit")
        finally:
            child.close()
        child = None

        stage = "Verificação no FTP"
        if not verify_upload(filename):
            raise RuntimeError(f"Upload não confirmado no FTP ({filename}). "
                               f"Saída da OLT: {' '.join(output.split())[-200:]}")

        log(f"✅ Backup realizado com sucesso: {olt['name']}")

        # Só limpa backups antigos depois de confirmar que o novo existe.
        cleanup_old_backups(olt["name"], keep=KEEP_BACKUPS)
        return True

    except Exception as e:
        if isinstance(e, (pexpect.TIMEOUT, pexpect.EOF)):
            # str(e) do pexpect despeja o buffer inteiro; resume e redige.
            first = str(e).splitlines()[0] if str(e) else type(e).__name__
            tail = " ".join(redact(getattr(child, "before", "") or "").split())[-200:]
            err = f"{first} | última saída da OLT: {tail or 'N/A'}"
        else:
            err = redact(e)
        log(f"❌ ERRO no backup {olt['name']} (etapa: {stage}): {err}")
        send_alert(olt, stage, err)
        return False
    finally:
        if child is not None:
            try:
                child.close(force=True)
            except Exception:
                pass


# =========================
# CHECK (validação pós-atualização da VM)
# =========================
def run_check():
    ok = True
    for err in validate_global_config():
        log(f"❌ {err}")
        ok = False
    for name in load_olt_list():
        problems = validate_olt(load_olt_credentials(name))
        if problems:
            log(f"❌ OLT {name}: {'; '.join(problems)}")
            ok = False
        else:
            log(f"✔️ OLT {name}: configuração OK")

    for binary in ("telnet", "ssh"):
        found = any(os.access(os.path.join(p, binary), os.X_OK)
                    for p in os.environ.get("PATH", "").split(os.pathsep))
        log(("✔️" if found else "⚠️") + f" binário {binary} {'encontrado' if found else 'NÃO encontrado'}")
        if binary == "telnet" and not found:
            ok = False

    try:
        ftp = ftp_connect()
        log(f"✔️ FTP OK ({'FTPS' if FTP_TLS else 'FTP'}) em {FTP_IP}:{FTP_PORT}")
        ftp.quit()
    except Exception as e:
        log(f"❌ FTP falhou: {e}")
        ok = False

    if SMTP_SERVER:
        try:
            with smtp_open():
                pass
            log(f"✔️ SMTP OK em {SMTP_SERVER}:{SMTP_PORT} ({_smtp_security()})")
        except Exception as e:
            log(f"❌ SMTP falhou: {e}")
            ok = False
    return ok


# =========================
# MAIN
# =========================
def main():
    global _SECRETS
    parser = argparse.ArgumentParser(description="Backup automático de OLTs FiberHome")
    parser.add_argument("--check", action="store_true",
                        help="valida configuração, FTP e SMTP sem acessar as OLTs")
    args = parser.parse_args()

    os.umask(0o077)  # qualquer arquivo criado pelo script fica só para o dono
    _SECRETS = _collect_secrets()
    check_env_permissions()

    # Impede duas execuções simultâneas (cron + manual, por exemplo)
    lock = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("⚠️ Já existe uma execução em andamento. Saindo.")
        return 2

    if args.check:
        return 0 if run_check() else 1

    errors = validate_global_config()
    if errors:
        for err in errors:
            log(f"❌ Configuração inválida: {err}")
        send_email("[BACKUP OLT] Configuração inválida",
                   "A rotina não foi executada:\n- " + "\n- ".join(errors))
        return 1

    olt_names = load_olt_list()
    if not olt_names:
        log("Nenhuma OLT configurada em OLT_LIST no arquivo .env")
        return 1

    success_list, fail_list = [], []
    for name in olt_names:
        olt = load_olt_credentials(name)
        (success_list if backup_olt(olt) else fail_list).append(name)

    send_summary_email(success_list, fail_list)
    return 0 if not fail_list else 1


if __name__ == "__main__":
    sys.exit(main())
