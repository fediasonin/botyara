import ipaddress
import json
import re
import smtplib
import dns.resolver
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters


with open('config/credentials.json', 'r') as file:
    creds = json.load(file)

SMTP_SERVER = creds["smtp_server"]
SMTP_PORT = creds["smtp_port"]
SENDER_LOGIN = creds["sender_login"]
SENDER_EMAIL = creds["sender_email"]
SENDER_PASSWORD = creds["sender_password"]
TARGET_CHAT_ID = "-4019202782"
TARGET_THREAD_ID = 3
PATTERN = r"""
    Имя\sпользователя:\s(?P<username>\w+)\s*
    Исходящий\sIP\sадрес:\s(?P<ip>\d+\.\d+\.\d+\.\d+)\s*
    ВПН\sточка\sвхода:\s(?P<vpn>[^\n]+)
"""
EMAIL_REGEX = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"


def ip_in_list(src_ip, ip_list):
    src_ip = ipaddress.ip_address(src_ip)

    for ip_item in ip_list:
        if isinstance(ip_item, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
            if src_ip in ip_item:
                return True
        elif isinstance(ip_item, tuple):
            start_ip, end_ip = ip_item
            if start_ip <= src_ip <= end_ip:
                return True
        else:
            if src_ip == ip_item:
                return True

    return False


def parse_ip_file(filename):
    with open(filename, 'r') as f:
        lines = f.readlines()

    parsed_ips = []

    for line in lines:
        line = line.strip()
        if '/' in line:
            address, mask = line.split('/')
            network = f"{address}/{mask}"
            parsed_ips.append(ipaddress.ip_network(network, strict=False))
        elif ':' in line:
            start_address, end_address = line.split(':')
            start_ip = ipaddress.ip_address(start_address)
            end_ip = ipaddress.ip_address(end_address)
            parsed_ips.append((start_ip, end_ip))
        else:
            parsed_ips.append(ipaddress.ip_address(line))

    return parsed_ips


def is_valid_email(email):
    if not re.match(EMAIL_REGEX, email):
        return False, "Неправильный формат email"
    domain = email.split('@')[-1]
    try:
        dns.resolver.resolve(domain, 'MX')
        return True, None
    except dns.resolver.NoAnswer:
        return False, f"Не удалось найти MX-запись для домена {domain}"
    except dns.resolver.NXDOMAIN:
        return False, f"Домен {domain} не существует"
    except Exception as e:
        return False, f"Ошибка при проверке домена: {str(e)}"


def send_email(to_email, subject, body):
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = to_email
    msg['Subject'] = subject

    msg.attach(MIMEText(body, 'plain'))
    server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
    server.ehlo()
    server.starttls()
    server.ehlo()
    server.login(SENDER_LOGIN, SENDER_PASSWORD)
    server.sendmail(SENDER_EMAIL, to_email, msg.as_string())
    server.quit()


async def send_telegram_notification(context, ip):
    notification_message = (
                            f"Во временный блок брут\n"
                            f"{ip}"
                            )
    await context.bot.send_message(chat_id=TARGET_CHAT_ID, text=notification_message)


async def start(update: Update, context):
    await update.message.reply_text(
        "Привет! Отправь мне сообщение с именем пользователя, и я отправлю ему уведомление на email.")


async def parse_message(update: Update, context):
    chat_id = str(update.message.chat_id)
    thread_id = update.message.message_thread_id
    if chat_id == TARGET_CHAT_ID:
        return
    if chat_id == TARGET_CHAT_ID or thread_id != TARGET_THREAD_ID:
        return

    subject = "Попытки подбора пароля"
    message_text = update.message.text
    ip_whitelist = parse_ip_file("config/filtered_addresses.txt")

    match = re.search(PATTERN, message_text, re.VERBOSE)

    if match:
        data = match.groupdict()
        ip_address = data['ip']
        if ip_in_list(ip_address, ip_whitelist):
            await update.message.reply_text(f"IP {ip_address} находится в белом списке. Отправка сообщения не требуется.")
            return

        username = data['username']
        email = f"{username}@mosreg.ru"
        shluz = data['vpn']
        is_valid, error_message = is_valid_email(email)
        if not is_valid:
            await update.message.reply_text(f"Невалидный email: {error_message}")
            return
        try:
            email_body = (
                f"Добрый день, под вашей учетной записью (логин, указанный при входе: {username}) "
                f"превышено число неудачных попыток подключения с IP {ip_address} к VPN шлюзу {shluz}, "
                "поэтому данный IP был заблокирован. Для разблокировки смените пароль в личном кабинете и оставьте заявку в support.mosreg.ru."
            )
            send_email(email, subject, email_body)
            await update.message.reply_text(f"Сообщение отправлено на {email}.")
            await send_telegram_notification(context, ip_address)
        except Exception as e:
            await update.message.reply_text(f"Не удалось отправить сообщение на {email}. Ошибка: {str(e)}")
    else:
        await update.message.reply_text("Не удалось распознать сообщение. Проверьте формат.")


def main():
    app = ApplicationBuilder().token("7464199250:AAHuudpzjsRuyryhNXntmCR8TV_umM2JzMI").build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, parse_message))

    app.run_polling()


if __name__ == "__main__":
    main()