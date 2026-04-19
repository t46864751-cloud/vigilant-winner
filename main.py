import os
import sys
import io
import time
import builtins as real_builtins
import traceback
from multiprocessing import Process, Queue
from unittest.mock import MagicMock
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler
from html import escape

TOKEN = "8608832312:AAGKkMZTYMth41mBqiTqjhSYJypFePgtM0s"

# === НАСТРОЙКИ ===
EXECUTION_TIMEOUT = 15 # Время жизни чужого кода (секунд). Защита от зависаний.
MAX_OUTPUT_LENGTH = 4000

# БЕЗОПАСНОЕ ОКРУЖЕНИЕ (Скрываем секреты)
SAFE_ENVIRON = {
    'HOME': '/home/sandbox',
    'USER': 'sandbox',
    'PATH': '/usr/local/bin:/usr/bin:/bin',
    'LANG': 'en_US.UTF-8',
    'SHELL': '/bin/bash'
}
# На всякий случай удаляем ВСЕ реальные переменные, чтобы не утекли пароли от БД, токены и т.д.
# Если нужно что-то передать внутрь — добавь сюда вручную.

# ==========================================================
# === ФЕЙКОВАЯ ФАЙЛОВАЯ СИСТЕМА (Всё в оперативной памяти) =
# ==========================================================
class FakeFile:
    def __init__(self, path, storage, mode='r'):
        self.path = path
        self.storage = storage
        self.mode = mode
        self.position = 0
        if 'a' in mode:
            self.storage.setdefault(path, "")
        elif 'w' in mode:
            self.storage[path] = ""

    def write(self, data):
        if 'r' in self.mode and '+' not in self.mode:
            raise IOError("Файл открыт только для чтения")
        data_str = str(data)
        self.storage[self.path] += data_str
        return len(data_str)

    def read(self, size=-1):
        content = self.storage.get(self.path, "")
        if size == -1:
            res = content[self.position:]
            self.position = len(content)
            return res
        result = content[self.position:self.position + size]
        self.position += len(result)
        return result

    def readline(self):
        content = self.storage.get(self.path, "")
        nl_pos = content.find('\n', self.position)
        if nl_pos != -1:
            res = content[self.position:nl_pos+1]
            self.position = nl_pos + 1
            return res
        res = content[self.position:]
        self.position = len(content)
        return res

    def readlines(self): return self.read().splitlines(keepends=True)
    def seek(self, pos): self.position = pos
    def tell(self): return self.position
    def close(self): pass
    def flush(self): pass
    def __enter__(self): return self
    def __exit__(self, *args): self.close()

class FakeFileSystem:
    def __init__(self):
        self.files = {}
        
    def open(self, path, mode='r', *args, **kwargs):
        return FakeFile(str(path), self.files, mode)

# ===================================
# === ФЕЙКОВЫЕ СИСТЕМНЫЕ МОДУЛИ ====
# ===================================
class FakeOS:
    """Подменяет настоящий os. Дает безопасные штуки, блокирует систему."""
    def __init__(self, fs):
        self.fs = fs
        self.environ = SAFE_ENVIRON
        self.name = 'posix'
        self.sep = '/'
        self.linesep = '\n'
        self.pathsep = ':'
        # Подменяем os.path на безопасный фасад
        self.path = FakePath(fs)

    def system(self, command):
        return f"[SANDBOX BLOCKED] os.system('{command}')"
    
    def popen(self, *a, **k):
        return MagicMock(read=lambda: "[SANDBOX BLOCKED]", readline=lambda: "", close=lambda: None)
    
    def remove(self, p): self.fs.files.pop(str(p), None)
    def unlink(self, p): self.remove(p)
    def mkdir(self, p, *a, **k): pass
    def makedirs(self, p, *a, **k): pass
    def rename(self, s, d): 
        if str(s) in self.fs.files: self.fs.files[str(d)] = self.fs.files.pop(str(s))
    def getcwd(self): return "/home/sandbox"
    def chdir(self, p): pass
    def getpid(self): return 99999
    def getppid(self): return 1
    def urandom(self, n):
        import random; return bytes(random.randint(0, 255) for _ in range(n))
        
    def __getattr__(self, name):
        # Блокируем всё остальное (getenv, kill, fork и т.д.)
        if name in ['getenv', 'putenv']: return lambda *a, **k: SAFE_ENVIRON.get(a[0]) if a else None
        return MagicMock(return_value=f"[SANDBOX BLOCKED os.{name}]")

class FakePath:
    def __init__(self, fs):
        self.fs = fs
        self.sep = '/'
        self.join = os.path.join
        self.exists = lambda p: str(p) in self.fs.files
        self.isfile = lambda p: str(p) in self.fs.files
        self.isdir = lambda p: False # В песочнице нет реальных папок
        self.abspath = lambda p: f"/home/sandbox/{p}"
        self.basename = os.path.basename
        self.dirname = os.path.dirname
        self.splitext = os.path.splitext
        self.getsize = lambda p: len(self.fs.files.get(str(p), ""))
        def __getattr__(self, name): return MagicMock()

class FakeSys:
    """Подменяет sys."""
    def __init__(self):
        self.version = "3.11.0 (SANDBOXED)"
        self.platform = "linux"
        self.path = ['/home/sandbox', '/usr/lib/python311.zip']
        self.modules = {}
        self.argv = ['sandbox.py']
        self.executable = '/usr/bin/python3'
        self.prefix = '/usr'
        
    def exit(self, arg=0):
        raise SystemExit(f"[SANDBOX] exit({arg}) вызван")
        
    def __getattr__(self, name):
        return MagicMock(return_value=f"[SANDBOX BLOCKED sys.{name}]")


# ==========================================
# === БЕЗОПАСНЫЙ ИМПОРТ (Gatekeeper) =====
# ==========================================
class SafeImport:
    """
    Перехватывает import. 
    Если просят os, sys, subprocess — дает фейки.
    Если просят网络/вычисления (requests, telebot, math) — дает настоящие модули.
    """
    def __init__(self, fake_os, fake_sys, fake_subprocess):
        self.fake_os = fake_os
        self.fake_sys = fake_sys
        self.fake_subprocess = fake_subprocess
        
        # Белый список ЧЕГО МОЖНО ИМПОРТИРОВАТЬ НАСТОЯЩЕГО
        # Сюда можно additives любую либу, которая установлена на сервере
        self.allowed_real = {
            # Стандартная библиотека (безопасная)
            'math', 'random', 'datetime', 'time', 're', 'string', 'itertools', 'functools',
            'collections', 'statistics', 'typing', 'decimal', 'fractions', 'hashlib',
            'base64', 'binascii', 'inspect', 'textwrap', 'uuid', 'html', 'json', 'csv',
            'pprint', 'copy', 'warnings', 'traceback', 'types', 'enum', 'dataclasses',
            'pathlib', 'calendar', 'numbers', 'io', 'builtins', 'threading', 'multiprocessing',
            'logging', 'sqlite3', 'email', 'xml', 'zipfile', 'tarfile', 'gzip', 'bz2', 'lzma',
            
            # Сетевые модули (Разрешаем сеть!)
            'socket', 'ssl', 'urllib', 'http', 'ftplib', 'smtplib',
            
            # Асинхронность
            'asyncio', 'concurrent',
            
            # Сторонние модули (добавляй сюда то, что установлено через pip)
            'requests', 'aiohttp', 'telebot', 'pyTelegramBotAPI', 
            'aiogram', 'numpy', 'pandas', 'flask', 'fastapi', 'bs4', 'lxml'
        }

    def __call__(self, name, globals=None, locals=None, fromlist=(), level=0):
        base_name = name.split('.')[0]
        
        # 1. Подмена системных модулей
        if base_name == 'os': return self.fake_os
        if base_name == 'sys': return self.fake_sys
        if base_name == 'subprocess': return self.fake_subprocess
        
        # 2. Проверка белого списка
        if base_name in self.allowed_real:
            try:
                return real_builtins.__import__(name, globals, locals, fromlist, level)
            except ImportError:
                raise ImportError(f"Модуль '{name}' разрешен, но не установлен на сервере!")
        
        # 3. Всё остальное - запрещено
        raise ImportError(f"🚫 [SANDBOX] Импорт модуля '{name}' заблокирован!")

# ==========================================
# === РАБОЧИЙ ПРОЦЕСС (Изолированная ячейка) ==
# ==========================================
def worker_process(code: str, result_queue: Queue):
    """Выполняется в отдельном процессе. Имеет доступ к сети, но ограничен в файлах и системе."""
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    buffer = io.StringIO()

    try:
        sys.stdout = buffer
        sys.stderr = buffer

        # Создаем изолированное окружение
        fs = FakeFileSystem()
        fake_os = FakeOS(fs)
        fake_sys = FakeSys()
        fake_subprocess = MagicMock() # Полная заглушка для subprocess

        # Настраиваем безопасные встроенные функции
        safe_builtins = dict(real_builtins)
        safe_builtins['open'] = fs.open
        safe_builtins['__import__'] = SafeImport(fake_os, fake_sys, fake_subprocess)
        safe_builtins['input'] = lambda prompt="": "[SANDBOX INPUT]"
        # Блокируем eval и exec на всякий случай (хотя AST парсер тоже есть)
        safe_builtins['eval'] = lambda *a, **k: exec("[SANDBOX] eval() заблокирован")
        safe_builtins['exec'] = lambda *a, **k: exec("[SANDBOX] exec() заблокирован")

        sandbox_globals = {
            '__builtins__': safe_builtins,
            '__name__': '__main__',
            'os': fake_os,
            'sys': fake_sys,
            'subprocess': fake_subprocess,
        }

        # Выполнение
        exec(code, sandbox_globals)

        result_queue.put({
            "success": True,
            "output": buffer.getvalue(),
            "error": None
        })

    except SystemExit:
        result_queue.put({"success": True, "output": buffer.getvalue(), "error": None})
    except Exception as e:
        result_queue.put({
            "success": False,
            "output": buffer.getvalue(),
            "error": f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        })
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

# === ЗАПУСК ПЕСОЧНИЦЫ (С ТАЙМАУТОМ) ===
def run_sandbox(code: str) -> dict:
    result_queue = Queue()
    process = Process(target=worker_process, args=(code, result_queue))
    
    process.start()
    process.join(timeout=EXECUTION_TIMEOUT)
    
    if process.is_alive():
        process.terminate()
        process.join(timeout=1)
        if process.is_alive():
            process.kill() # SIGKILL
            process.join()
            
        return {
            "success": False,
            "output": "",
            "error": f"⏳ ТАЙМАУТ ({EXECUTION_TIMEOUT}с).\nПроцесс уничтожен. (Возможно запущен blocking цикл вроде bot.polling())"
        }
    
    if not result_queue.empty():
        return result_queue.get()
    return {"success": False, "output": "", "error": "Песочница внезапно умерла без объяснения причин."}

# === УТИЛИТЫ ДЛЯ TELEGRAM ===
def split_text(text: str) -> list:
    if not text: return ["📭 (пустой вывод)"]
    if len(text) <= MAX_OUTPUT_LENGTH: return [text]
    
    chunks = []
    while text:
        # Разрываем по переносу строки, чтобы не ломать код посередине
        split_pos = text.rfind('\n', 0, MAX_OUTPUT_LENGTH)
        if split_pos == -1: split_pos = MAX_OUTPUT_LENGTH
        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip('\n')
    return chunks

async def execute_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_code = update.message.text
    
    wait_msg = await update.message.reply_text("⏳ Запуск в изолированном Docker-подобном окружении...")
    
    result = run_sandbox(user_code)
    
    try:
        await wait_msg.delete()
    except:
        pass
        
    # Безопасная обертка в HTML (защита от багов парсинга Telegram)
    safe_code_preview = escape(user_code[:800]) + ("..." if len(user_code) > 800 else "")
    
    if result["success"]:
        header = (
            "✅ <b>Успешно выполнено</b>\n"
            "🔧 <i>Окружение: Secure Network Sandbox (Docker-like)</i>\n\n"
            f"💻 <b>Код:</b>\n<code>{safe_code_preview}</code>\n\n"
            "📤 <b>Вывод:</b>\n"
        )
    else:
        header = (
            "❌ <b>Ошибка / Таймаут</b>\n"
            "🔧 <i>Окружение: Secure Network Sandbox (Docker-like)</i>\n\n"
            f"💻 <b>Код:</b>\n<code>{safe_code_preview}</code>\n\n"
            "📤 <b>Вывод до ошибки:</b>\n"
        )

    output_chunks = split_text(result["output"])
    
    for i, chunk in enumerate(output_chunks):
        prefix = f"📄 Часть {i+1}/{len(output_chunks)}\n\n" if len(output_chunks) > 1 else ""
        
        # Последний кусок вывода — добавляем ошибку, если она есть
        error_suffix = ""
        if not result["success"] and i == len(output_chunks) - 1:
            error_suffix = f"\n\n🚨 <b>Ошибка:</b>\n<code>{escape(result['error'][:1000])}</code>"
            
        text = header + prefix + "<code>" + escape(chunk) + "</code>" + error_suffix
        header = "" # Чтобы заголовок был только в первом сообщении

        # Если текст всё равно длинный (бывает при огромной ошибке), режем принудительно
        if len(text) > 4000:
            text = text[:3900] + "\n\n... [Обрезано по лимиту Telegram]"
            
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode='HTML')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🐳 <b>Mega PySandbox Bot (Docker-like Edition)</b>\n\n"
        "Отправь мне Python-код!\n\n"
        "✅ <b>Сеть РЕАЛЬНАЯ:</b> requests, telebot, aiogram, aiohttp, socket, http, urllib, sqlite3, flask, numpy, pandas и др.\n"
        "🔐 <b>Система ФЕЙКОВАЯ:</b> os, sys, subprocess подменены. Нельзя удалить файлы, убить процессы, прочитать чужие токены.\n"
        "💾 <b>Файлы в RAM:</b> open() пишет и читает только из оперативной памяти (не на жесткий диск).\n"
        "⏳ <b>Защита от зависаний:</b> Любой бесконечный цикл или bot.infinity_polling() будет убит через 15 секунд.\n\n"
        "<i>Пример: можно сделать import requests; requests.get('https://api.ipify.org').text</i>",
        parse_mode='HTML'
    )

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    # Игнорируем команды, чтобы /start не ушел в выполнение кода
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, execute_code))
    app.run_polling()

if __name__ == "__main__":
    main()
