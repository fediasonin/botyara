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
TARGET_CHAT_ID = creds["target_chat_id"]
MAIN_CHAT_ID = creds["main_chat_id"]
API_TOKEN = creds["api_token"]
TARGET_THREAD_ID = 3
PATTERN = r"""
    Имя\sпользователя:\s(?P<username>[\w\.]+)\s*
    Исходящий\sIP\sадрес:\s(?P<ip>\d+\.\d+\.\d+\.\d+)\s*
    ВПН\sточка\sвхода:\s(?P<vpn>[^\n]+)
"""
EMAIL_REGEX = r"^[a-zA-Z0-9._+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"


def ip_formatter(ip_address):
    return re.sub(r'\.(?=[0-9]+$)', '[.]', ip_address)


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
    if thread_id != TARGET_THREAD_ID and chat_id != MAIN_CHAT_ID:
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
            # Приоритетное уведомление в Telegram
            await send_telegram_notification(context, ip_address)
            send_status = f"Уведомление отправлено в Telegram для IP {ip_address}."

            # Попытка отправить почту
            email_body = (
                f"Добрый день, средствами мониторинга зафиксировано превышение числа неудачных попыток подключения учетной записи {username} с IP адреса {ip_formatter(ip_address)} к VPN шлюзу {shluz}, "
                "поэтому данный IP был заблокирован. Для разблокировки необходимо: "
                f"1. Сменить пароль от учётной записи {username} на pass.mosreg.ru"
                "2. Провести полную антивирусную проверку рабочего хоста. "
                "3. Если нет САЗ, установить его и провести полную проверку. "
                "4. Написать заявку в support.mosreg.ru с приложением скриншота результатов проверки, содержащего: "
                "   - Результаты проверки. "
                "   - Время проверки. "
                "   - IP-адрес хоста. "
                f"5. Указать IP-адрес, который необходимо разблокировать."
            )

            send_email(email, subject, email_body)
            send_status += f"\nСообщение также отправлено на {email}."
        except Exception as e:
            send_status += f"\nНе удалось отправить сообщение на {email}. Ошибка: {str(e)}"

        await update.message.reply_text(send_status)
    else:
        await update.message.reply_text("Не удалось распознать сообщение. Проверьте формат.")


async def get_chat_id(update: Update, context):
    chat_id = update.message.chat_id
    await update.message.reply_text(f"Ваш chat ID: {chat_id}")

async def get_thread_id(update: Update, context):
    thread_id = update.message.message_thread_id
    await update.message.reply_text(f"Текущий THREAD_ID: {thread_id}")

def main():
    app = ApplicationBuilder().token(API_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("getchatid", get_chat_id))
    app.add_handler(CommandHandler("getthreadid", get_thread_id))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, parse_message))

    app.run_polling()


if __name__ == "__main__":
    main()
