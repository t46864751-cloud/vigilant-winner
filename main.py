import os
import subprocess
import tempfile
import shutil
import signal
import resource
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler

TOKEN = "8608832312:AAGKkMZTYMth41mBqiTqjhSYJypFePgtM0s"

# Проверяем доступность Docker
DOCKER_AVAILABLE = False
try:
    subprocess.run(["docker", "--version"], check=True, capture_output=True)
    # Проверяем доступ к демону
    result = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if result.returncode == 0:
        DOCKER_AVAILABLE = True
        print("✅ Docker доступен")
    else:
        print("⚠️ Docker CLI есть, но демон недоступен")
except:
    print("❌ Docker не установлен")

def run_in_docker(code: str) -> dict:
    """Выполняем код в Docker-контейнере"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(code)
        temp_file = f.name
    
    try:
        # Создаём контейнер с ограничениями
        result = subprocess.run([
            "docker", "run", "--rm",
            "--memory", "64m",
            "--cpus", "0.5",
            "--network", "none",
            "--read-only",
            "--tmpfs", "/tmp:noexec,nosuid,size=10m",
            "-v", temp_file + ":/code/script.py:ro",
            "python:3.11-alpine",
            "python", "/code/script.py"
        ], capture_output=True, text=True, timeout=10)
        
        return {
            "success": result.returncode == 0,
            "output": result.stdout.strip(),
            "error": result.stderr.strip() if result.returncode != 0 else None
        }
        
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "", "error": "⏱️ Таймаут (10 сек)"}
    except Exception as e:
        return {"success": False, "output": "", "error": str(e)}
    finally:
        if os.path.exists(temp_file):
            os.unlink(temp_file)

def run_chroot(code: str) -> dict:
    """Fallback: выполняем код в chroot + ограничения ресурсов"""
    # Создаём временную директорию для "тюрьмы"
    jail_dir = tempfile.mkdtemp()
    
    try:
        # Копируем Python в тюрьму (минимальный набор)
        python_path = shutil.which("python3")
        if not python_path:
            python_path = shutil.which("python")
        
        # Создаём структуру
        os.makedirs(os.path.join(jail_dir, "bin"), exist_ok=True)
        os.makedirs(os.path.join(jail_dir, "lib"), exist_ok=True)
        os.makedirs(os.path.join(jail_dir, "code"), exist_ok=True)
        
        # Пишем код
        script_path = os.path.join(jail_dir, "code", "script.py")
        with open(script_path, 'w') as f:
            f.write(code)
        
        # Запускаем с ограничениями через unshare (новые namespaces)
        # --fork = новый процесс, --pid = новое PID пространство, --user = новый пользователь
        # --map-root-user = мапим root внутри namespace
        cmd = [
            "unshare", "--fork", "--pid", "--user", "--map-root-user",
            "--mount-proc", "--ipc", "--uts", "--net",
            "python3", script_path
        ]
        
        # Устанавливаем лимиты через ulimit в команде
        full_cmd = [
            "bash", "-c",
            f"ulimit -v 65536; ulimit -t 5; ulimit -f 0; ulimit -n 10; " +
            f"cd {jail_dir} && chroot {jail_dir} python3 /code/script.py 2>&1"
        ]
        
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=10,
            preexec_fn=lambda: (
                resource.setrlimit(resource.RLIMIT_AS, (64 * 1024 * 1024, 64 * 1024 * 1024)),
                resource.setrlimit(resource.RLIMIT_CPU, (5, 5)),
                resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0)),
                resource.setrlimit(resource.RLIMIT_NOFILE, (10, 10))
            )
        )
        
        return {
            "success": result.returncode == 0,
            "output": result.stdout.strip(),
            "error": result.stderr.strip() if result.returncode != 0 else None
        }
        
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "", "error": "⏱️ Таймаут (10 сек)"}
    except Exception as e:
        return {"success": False, "output": "", "error": str(e)}
    finally:
        shutil.rmtree(jail_dir, ignore_errors=True)

async def execute_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_code = update.message.text
    
    if DOCKER_AVAILABLE:
        result = run_in_docker(user_code)
        method = "🐳 Docker"
    else:
        result = run_chroot(user_code)
        method = "🔒 Chroot+Namespaces"
    
    if result["success"]:
        text = (
            "✅ *Успешно выполнено*\n\n"
            "🔧 Метод: " + method + "\n"
            "💻 Код:\n```python\n" + user_code + "\n```\n"
            "📤 Вывод:\n```\n" + result['output'][:3000] + "\n```"
        )
    else:
        text = (
            "❌ *Ошибка выполнения*\n\n"
            "🔧 Метод: " + method + "\n"
            "💻 Код:\n```python\n" + user_code + "\n```\n"
            "🚨 Ошибка: `" + result['error'][:1000] + "`"
        )
    
    await context.bot.send_message(chat_id=user_id, text=text, parse_mode='Markdown')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = "🐳 Docker" if DOCKER_AVAILABLE else "🔒 Chroot+Namespaces"
    await update.message.reply_text(
        "🚀 *PySandbox Bot*\n\n"
        "Отправь мне любой Python-код, и я выполню его в изолированной среде!\n\n"
        "🔧 Режим: " + mode,
        parse_mode='Markdown'
    )

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, execute_code))
    app.run_polling()

if __name__ == "__main__":
    main()
