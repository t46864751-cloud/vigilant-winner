import os
import subprocess
import tempfile
import shutil
import signal
import resource
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler

TOKEN = "8608832312:AAGKkMZTYMth41mBqiTqjhSYJypFePgtM0s"

def run_isolated(code: str) -> dict:
    """Выполняем код в изолированном chroot с ограничениями ресурсов"""
    # Создаём временную директорию для "тюрьмы"
    jail_dir = tempfile.mkdtemp()
    
    try:
        # Создаём минимальную структуру
        bin_dir = os.path.join(jail_dir, "bin")
        lib_dir = os.path.join(jail_dir, "lib")
        code_dir = os.path.join(jail_dir, "code")
        tmp_dir = os.path.join(jail_dir, "tmp")
        
        os.makedirs(bin_dir, exist_ok=True)
        os.makedirs(lib_dir, exist_ok=True)
        os.makedirs(code_dir, exist_ok=True)
        os.makedirs(tmp_dir, exist_ok=True)
        
        # Пишем код пользователя
        script_path = os.path.join(code_dir, "script.py")
        with open(script_path, 'w') as f:
            f.write(code)
        
        # Запускаем через unshare (новые namespaces) + chroot
        # --fork = новый процесс
        # --pid = новое PID пространство (изоляция процессов)
        # --user = новый пользовательский namespace
        # --map-root-user = мапим текущего пользователя в root внутри namespace
        # --mount-proc = монтируем /proc
        # --ipc = изоляция IPC
        # --uts = изоляция hostname
        # --net = изоляция сети (нет сети)
        # --mount = изоляция mount points
        
        # Сначала копируем python3 бинарник и библиотеки в jail
        python_path = shutil.which("python3") or shutil.which("python")
        if python_path:
            shutil.copy2(python_path, os.path.join(bin_dir, "python3"))
            # Копируем зависимости (упрощённо)
            os.system(f"ldd {python_path} 2>/dev/null | grep '=> /' | awk '{{print $3}}' | xargs -I {{}} cp -v {{}} {lib_dir}/ 2>/dev/null || true")
        
        # Создаём простой скрипт-обёртку который сделает chroot
        wrapper = os.path.join(jail_dir, "run.sh")
        with open(wrapper, 'w') as f:
            f.write("#!/bin/bash\n")
            f.write("chroot /jail python3 /code/script.py 2>&1\n")
        
        # Запускаем через bash с ограничениями
        cmd = [
            "bash", "-c",
            f"""
            export PATH=/usr/bin:/bin
            # Создаём namespace и chroot
            unshare --fork --pid --user --map-root-user --mount-proc --ipc --uts --net --mount bash -c '
                cd {jail_dir}
                mkdir -p old_root
                mount --bind {jail_dir} {jail_dir}
                pivot_root . old_root
                cd /
                umount -l old_root 2>/dev/null
                python3 /code/script.py
            ' 2>&1
            """
        ]
        
        # Запускаем с жёсткими лимитами ресурсов
        def set_limits():
            # 64 MB RAM
            resource.setrlimit(resource.RLIMIT_AS, (64 * 1024 * 1024, 64 * 1024 * 1024))
            # 5 секунд CPU
            resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
            # Нельзя создавать файлы
            resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
            # Макс 10 открытых файлов
            resource.setrlimit(resource.RLIMIT_NOFILE, (10, 10))
            # Макс 1 процесс
            resource.setrlimit(resource.RLIMIT_NPROC, (1, 1))
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            preexec_fn=set_limits
        )
        
        return {
            "success": result.returncode == 0,
            "output": result.stdout.strip() or "📭 (пустой вывод)",
            "error": result.stderr.strip() if result.returncode != 0 else None
        }
        
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "", "error": "⏱️ Таймаут (10 сек)"}
    except Exception as e:
        return {"success": False, "output": "", "error": str(e)}
    finally:
        # Чистим
        shutil.rmtree(jail_dir, ignore_errors=True)

async def execute_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_code = update.message.text
    
    result = run_isolated(user_code)
    
    if result["success"]:
        text = (
            "✅ *Успешно выполнено*\n\n"
            "🔧 Метод: 🔒 Isolated Sandbox\n"
            "💻 Код:\n```python\n" + user_code + "\n```\n"
            "📤 Вывод:\n```\n" + result['output'][:3000] + "\n```"
        )
    else:
        text = (
            "❌ *Ошибка выполнения*\n\n"
            "🔧 Метод: 🔒 Isolated Sandbox\n"
            "💻 Код:\n```python\n" + user_code + "\n```\n"
            "🚨 Ошибка: `" + result['error'][:1000] + "`"
        )
    
    await context.bot.send_message(chat_id=user_id, text=text, parse_mode='Markdown')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 *PySandbox Bot*\n\n"
        "Отправь мне любой Python-код, и я выполню его в изолированной среде!\n\n"
        "🔧 Режим: 🔒 Isolated Sandbox (chroot + namespaces + rlimits)",
        parse_mode='Markdown'
    )

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, execute_code))
    app.run_polling()

if __name__ == "__main__":
    main()
