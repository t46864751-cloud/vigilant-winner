import os
import subprocess
import time
import tempfile
import docker
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler

TOKEN = "8608832312:AAGKkMZTYMth41mBqiTqjhSYJypFePgtM0s"

def start_docker_daemon():
    """Запускаем Docker-демон внутри контейнера"""
    try:
        # Проверяем если уже запущен
        subprocess.run(["docker", "info"], check=True, capture_output=True)
        print("✅ Docker уже запущен")
        return True
    except:
        print("🚀 Запускаем Docker-демон...")
        
    # Запускаем dockerd в фоне
    subprocess.Popen(
        ["dockerd", "--host=unix:///var/run/docker.sock", "--storage-driver=vfs"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    
    # Ждём пока запустится
    for i in range(30):
        time.sleep(1)
        try:
            subprocess.run(["docker", "info"], check=True, capture_output=True)
            print("✅ Docker-демон запущен")
            return True
        except:
            continue
    
    print("❌ Не удалось запустить Docker")
    return False

def run_in_docker(code: str) -> dict:
    client = docker.from_env()
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(code)
        temp_file = f.name
    
    try:
        # Предварительно скачиваем образ если нет
        try:
            client.images.get("python:3.11-alpine")
        except:
            client.images.pull("python:3.11-alpine")
        
        container = client.containers.run(
            image="python:3.11-alpine",
            command="python /code/script.py",
            volumes={temp_file: {'bind': '/code/script.py', 'mode': 'ro'}},
            mem_limit="64m",
            cpu_quota=50000,
            network_mode="none",
            detach=True,
            auto_remove=True,
        )
        
        try:
            result = container.wait(timeout=10)
            logs = container.logs().decode('utf-8').strip()
            return {
                "success": result['StatusCode'] == 0,
                "output": logs,
                "error": None if result['StatusCode'] == 0 else "Exit code: " + str(result['StatusCode'])
            }
        except Exception as e:
            container.kill()
            return {"success": False, "output": "", "error": "⏱️ Таймаут (10 сек)"}
            
    finally:
        if os.path.exists(temp_file):
            os.unlink(temp_file)

async def execute_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_code = update.message.text
    
    result = run_in_docker(user_code)
    
    if result["success"]:
        text = (
            "✅ *Успешно выполнено*\n\n"
            "🐳 Метод: Docker\n"
            "💻 Код:\n```python\n" + user_code + "\n```\n"
            "📤 Вывод:\n```\n" + result['output'][:3000] + "\n```"
        )
    else:
        text = (
            "❌ *Ошибка выполнения*\n\n"
            "🐳 Метод: Docker\n"
            "💻 Код:\n```python\n" + user_code + "\n```\n"
            "🚨 Ошибка: `" + result['error'] + "`"
        )
    
    await context.bot.send_message(chat_id=user_id, text=text, parse_mode='Markdown')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 *PySandbox Bot*\n\n"
        "Отправь мне любой Python-код, и я выполню его в изолированном Docker-контейнере!\n\n"
        "🐳 Режим: Docker-in-Docker",
        parse_mode='Markdown'
    )

def main():
    # Запускаем Docker-демон перед стартом бота
    if not start_docker_daemon():
        print("❌ Критическая ошибка: Docker не запустился")
        return
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, execute_code))
    app.run_polling()

if __name__ == "__main__":
    main()
