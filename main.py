import os
import sys
import asyncio
import tempfile
import io
from contextlib import redirect_stdout
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler

TOKEN = "8608832312:AAGKkMZTYMth41mBqiTqjhSYJypFePgtM0s"

DOCKER_AVAILABLE = os.path.exists('/var/run/docker.sock')

if DOCKER_AVAILABLE:
    import docker
    client = docker.from_env()

if not DOCKER_AVAILABLE:
    from RestrictedPython import compile_restricted
    from RestrictedPython.Guards import safe_builtins

def run_in_docker(code: str) -> dict:
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(code)
        temp_file = f.name
    
    try:
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

def run_restricted(code: str) -> dict:
    try:
        restricted_globals = {
            "__builtins__": safe_builtins,
            "_getattr_": getattr,
            "_setattr_": setattr,
            "_delattr_": delattr,
            "_getitem_": lambda x, y: x[y],
            "_setitem_": lambda x, y, z: x.__setitem__(y, z),
            "_iter_unpack_sequence_": lambda x, y: x,
            "_unpack_sequence_": lambda x, y: x,
        }
        
        bytecode = compile_restricted(code, '<inline>', 'exec')
        if bytecode is None:
            return {"success": False, "output": "", "error": "🚫 Ошибка компиляции"}
        
        output_buffer = io.StringIO()
        with redirect_stdout(output_buffer):
            exec(bytecode, restricted_globals, {})
        
        return {
            "success": True,
            "output": output_buffer.getvalue().strip() or "📭 (пустой вывод)",
            "error": None
        }
        
    except Exception as e:
        return {"success": False, "output": "", "error": "⚠️ " + str(e)}

async def execute_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_code = update.message.text
    
    method = "🐳 Docker" if DOCKER_AVAILABLE else "🔒 RestrictedPython"
    
    if DOCKER_AVAILABLE:
        result = run_in_docker(user_code)
    else:
        result = run_restricted(user_code)
    
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
            "🚨 Ошибка: `" + result['error'] + "`"
        )
    
    await context.bot.send_message(chat_id=user_id, text=text, parse_mode='Markdown')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 *PySandbox Bot*\n\n"
        "Отправь мне любой Python-код, и я выполню его в изолированной среде!\n\n"
        + ('🐳 Режим: Docker' if DOCKER_AVAILABLE else '🔒 Режим: RestrictedPython'),
        parse_mode='Markdown'
    )

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, execute_code))
    app.run_polling()

if __name__ == "__main__":
    main()
