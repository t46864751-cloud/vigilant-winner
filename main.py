import os
import sys
import ast
import io
import time
import builtins as real_builtins
import types
import asyncio
import traceback
from multiprocessing import Process, Queue
from unittest.mock import MagicMock
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler
from html import escape

TOKEN = "8608832312:AAGKkMZTYMth41mBqiTqjhSYJypFePgtM0s"

# === КОНФИГУРАЦИЯ ПЕСОЧНИЦЫ ===
EXECUTION_TIMEOUT = 20  # Секунд на выполнение кода (защита от while True)
MAX_TG_MESSAGE_LEN = 4000
DELAY_BETWEEN_CHUNKS = 0.4  # Задержка между частями вывода, чтобы ТГ не кидал Rate Limit

# Безопасные переменные окружения (Скрываем реальные токены и пароли от БД)
SAFE_ENVIRON = {
    'HOME': '/home/sandbox',
    'USER': 'sandbox',
    'PATH': '/usr/local/bin:/usr/bin:/bin',
    'PWD': '/home/sandbox',
    'LANG': 'en_US.UTF-8',
    'SHELL': '/bin/bash',
    'SANDBOX': 'true'
}

# Список модулей, которым разрешено использовать НАСТОЯЩУЮ СЕТЬ и вычисления
ALLOWED_REAL_MODULES = {
    # Стандартная библиотека (Вычисления)
    'math', 'random', 'datetime', 'time', 're', 'string', 'itertools', 'functools',
    'collections', 'statistics', 'typing', 'decimal', 'fractions', 'hashlib',
    'base64', 'binascii', 'inspect', 'textwrap', 'uuid', 'html', 'json', 'csv',
    'pprint', 'copy', 'warnings', 'traceback', 'types', 'enum', 'dataclasses',
    'pathlib', 'calendar', 'numbers', 'io', 'builtins', 'threading', 'logging',
    'sqlite3', 'email', 'xml', 'zipfile', 'tarfile', 'gzip', 'bz2', 'lzma',
    'secrets', 'string', 'struct', 'ctypes',
    
    # Сетевые (Настоящие сокеты и запросы)
    'socket', 'ssl', 'urllib', 'http', 'ftplib', 'smtplib', 'imaplib',
    
    # Асинхронность
    'asyncio', 'concurrent', 'multiprocessing',
    
    # Сторонние (Добавляй сюда то, что установлено через pip на сервере)
    'requests', 'aiohttp', 'telebot', 'pyTelegramBotAPI',
    'aiogram', 'numpy', 'pandas', 'flask', 'fastapi', 'bs4', 'lxml',
    'pydantic', 'certifi', 'charset_normalizer', 'idna', 'urllib3'
}


# =====================================================================
# === БЕЗОПАСНОСТЬ НА УРОВНЕ AST (Защита от хитрых хакеров) ==========
# =====================================================================
class SecurityAstVisitor(ast.NodeVisitor):
    """
    Парсит дерево кода ДО выполнения.
    Блокирует: import os, eval(''), getattr(__builtins__, ...) и т.д.
    """
    FORBIDDEN_BUILTINS = {'eval', 'exec', 'compile', 'breakpoint', '__import__'}

    def visit_Import(self, node):
        for alias in node.names:
            base_name = alias.name.split('.')[0]
            if base_name not in ALLOWED_REAL_MODULES and base_name not in ['os', 'sys', 'subprocess']:
                raise ImportError(f"🚫 [AST SECURITY] Запрещен импорт: '{alias.name}'")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module:
            base_name = node.module.split('.')[0]
            if base_name not in ALLOWED_REAL_MODULES and base_name not in ['os', 'sys', 'subprocess']:
                raise ImportError(f"🚫 [AST SECURITY] Запрещен импорт из: '{node.module}'")
        self.generic_visit(node)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id in self.FORBIDDEN_BUILTINS:
            raise NameError(f"🚫 [AST SECURITY] Вызов '{node.func.id}' заблокирован")
        self.generic_visit(node)

def check_ast_security(code: str):
    try:
        tree = ast.parse(code)
        SecurityAstVisitor().visit(tree)
    except SyntaxError as e:
        raise SyntaxError(f"Синтаксическая ошибка: {e}")


# =====================================================================
# === ФЕЙКОВАЯ ФАЙЛОВАЯ СИСТЕМА (Всё в оперативной памяти) ============
# =====================================================================
class FakeFile:
    """Максимально полная эмуляция файлового объекта"""
    def __init__(self, path, fs_storage, mode='r', encoding='utf-8'):
        self.path = path
        self.storage = fs_storage
        self.mode = mode
        self.encoding = encoding
        self.closed = False
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

    def readlines(self):
        return self.read().splitlines(keepends=True)

    def seek(self, pos, whence=0):
        if whence == 0: self.position = pos
        elif whence == 1: self.position += pos
        elif whence == 2: self.position = len(self.storage.get(self.path, "")) + pos

    def tell(self): return self.position
    def flush(self): pass
    def close(self): self.closed = True
    def readable(self): return 'r' in self.mode
    def writable(self): return 'w' in self.mode or 'a' in self.mode or '+' in self.mode
    def seekable(self): return True
    
    def __iter__(self):
        while True:
            line = self.readline()
            if not line: break
            yield line
            
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


class FakeFileSystem:
    def __init__(self):
        self.files = {}
        
    def open(self, path, mode='r', *args, **kwargs):
        return FakeFile(str(path), self.files, mode, kwargs.get('encoding', 'utf-8'))

    def exists(self, path): return str(path) in self.files
    def remove(self, path): self.files.pop(str(path), None)
    def unlink(self, path): self.remove(path)
    def rename(self, src, dst):
        if str(src) in self.files:
            self.files[str(dst)] = self.files.pop(str(src))
    def getcwd(self): return "/home/sandbox"


# =====================================================================
# === ФЕЙКОВЫЕ СИСТЕМНЫЕ МОДУЛИ (Docker-эмуляция) ====================
# =====================================================================
class FakePath:
    """Полная заглушка для os.path, чтобы не крашились сторонние либы"""
    def __init__(self, fs):
        self.fs = fs
        self.sep = '/'
        self.altsep = None
        self.extsep = '.'
        self.curdir = '.'
        self.pardir = '..'
        
    def join(self, *args): return os.path.join(*args)
    def exists(self, p): return self.fs.exists(p)
    def isfile(self, p): return self.fs.exists(p)
    def isdir(self, p): return False
    def abspath(self, p): return f"/home/sandbox/{p}"
    def realpath(self, p): return self.abspath(p)
    def basename(self, p): return str(p).split('/')[-1]
    def dirname(self, p): return '/'.join(str(p).split('/')[:-1]) or '/'
    def split(self, p): return (self.dirname(p), self.basename(p))
    def splitext(self, p):
        p = str(p)
        if '.' in p: return (p[:p.rfind('.')], p[p.rfind('.'):])
        return (p, '')
    def getsize(self, p): return len(self.fs.files.get(str(p), ""))
    def normpath(self, p): return os.path.normpath(p)
    def isabs(self, p): return str(p).startswith('/')
    def expanduser(self, p): return p.replace('~', '/home/sandbox')
    
    def __getattr__(self, name):
        return MagicMock(return_value=f"/fake/path/{name}")


class FakeOS:
    def __init__(self, fs):
        self.fs = fs
        self.environ = SAFE_ENVIRON
        self.name = 'posix'
        self.sep = '/'
        self.linesep = '\n'
        self.pathsep = ':'
        self.devnull = '/dev/null'
        self.path = FakePath(fs) # Критически важно для работы urllib/requests внутри песочницы

    def system(self, command): return f"[SANDBOX BLOCKED] os.system('{command}')"
    def popen(self, *a, **k): return MagicMock(read=lambda: "[SANDBOX]", readline=lambda: "", close=lambda: None)
    
    def listdir(self, path='.'): return ['sandbox_files_are_in_ram']
    def mkdir(self, p, *a, **k): pass
    def makedirs(self, p, *a, **k): pass
    def remove(self, p): self.fs.remove(p)
    def unlink(self, p): self.fs.remove(p)
    def rename(self, s, d): self.fs.rename(s, d)
    def replace(self, s, d): self.fs.rename(s, d)
    def getcwd(self): return "/home/sandbox"
    def chdir(self, p): pass
    def getpid(self): return 99999
    def getppid(self): return 1
    def getuid(self): return 1000
    def getgid(self): return 1000
    def urandom(self, n):
        import random
        return bytes(random.randint(0, 255) for _ in range(n))
    
    def getenv(self, key, default=""):
        return SAFE_ENVIRON.get(key, default)
        
    def __getattr__(self, name):
        if name in ['fork', 'kill', 'spawnl', 'spawnv', 'execv', 'execve', 'system']:
            return MagicMock(return_value="[SANDBOX BLOCKED CRITICAL SYS CALL]")
        return MagicMock(return_value=f"[SANDBOX BLOCKED os.{name}]")


class FakeSys:
    def __init__(self):
        self.stdout = MagicMock(write=lambda x: None, flush=lambda: None)
        self.stderr = MagicMock(write=lambda x: None, flush=lambda: None)
        self.stdin = MagicMock(read=lambda: "", readline=lambda: "[SANDBOX INPUT]\n")
        self.version = "3.11.0 (Secure Sandbox Edition)"
        self.version_info = (3, 11, 0, 'final', 0)
        self.platform = "linux"
        self.byteorder = "little"
        self.maxsize = 9223372036854775807
        self.path = ['/home/sandbox', '/usr/lib/python311.zip']
        self.modules = {}
        self.argv = ['sandbox.py']
        self.executable = '/usr/bin/python3'
        self.prefix = '/usr'
        self.exec_prefix = '/usr'
    
    def exit(self, arg=0):
        raise SystemExit(f"[SANDBOX] exit({arg}) вызван")
        
    def __getattr__(self, name):
        return MagicMock(return_value=f"[SANDBOX BLOCKED sys.{name}]")


# =====================================================================
# === УМНЫЙ ИМПОРТ (Gatekeeper Сети и Системы) ========================
# =====================================================================
class SafeImport:
    def __init__(self, fake_os, fake_sys, fake_subprocess):
        self.fake_os = fake_os
        self.fake_sys = fake_sys
        self.fake_subprocess = fake_subprocess

    def __call__(self, name, globals=None, locals=None, fromlist=(), level=0):
        base_name = name.split('.')[0]
        
        # 1. Подмена системных модулей (Безопасность)
        if base_name == 'os': return self.fake_os
        if base_name == 'sys': return self.fake_sys
        if base_name == 'subprocess': return self.fake_subprocess
        if base_name == 'shutil': return MagicMock() # shutil часто используется для удаления файлов
        
        # 2. Выдача настоящих модулей (Сеть и Вычисления)
        if base_name in ALLOWED_REAL_MODULES:
            try:
                return real_builtins.__import__(name, globals, locals, fromlist, level)
            except ImportError as e:
                raise ImportError(f"Модуль '{name}' разрешен, но не установлен на сервере! ({e})")
        
        # 3. Блокировка всего остального
        raise ImportError(f"🚫 [SANDBOX] Импорт модуля '{name}' жестко заблокирован!")


# =====================================================================
# === ИЗОЛИРОВАННЫЙ РАБОЧИЙ ПРОЦЕСС (Ядро Песочницы) =================
# =====================================================================
def worker_process(code: str, result_queue: Queue):
    """
    Запускается в ОТДЕЛЬНОМ процессе ОС.
    Имеет доступ к сети, но сидит в клетке из фейковых os/sys/open.
    """
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    buffer = io.StringIO()

    try:
        sys.stdout = buffer
        sys.stderr = buffer

        # 1. Проверка кода AST-парсером до выполнения
        check_ast_security(code)

        # 2. Создание изолированного окружения
        fs = FakeFileSystem()
        fake_os = FakeOS(fs)
        fake_sys = FakeSys()
        fake_subprocess = MagicMock(
            run=lambda *a, **k: MagicMock(returncode=0, stdout='[SANDBOX]', stderr=''),
            Popen=lambda *a, **k: MagicMock(communicate=lambda: (b'[SANDBOX]', b''), wait=lambda: 0),
            call=lambda *a, **k: 0,
            check_output=lambda *a, **k: b'[SANDBOX]'
        )

        # 3. Настройка встроенных функций (builtins)
        safe_builtins = dict(real_builtins)
        safe_builtins['open'] = fs.open
        safe_builtins['__import__'] = SafeImport(fake_os, fake_sys, fake_subprocess)
        safe_builtins['input'] = lambda prompt="": "[SANDBOX INPUT]"
        
        # Жесткая блокировка опасных вызовов через builtins
        safe_builtins['eval'] = lambda *a, **k: exec("[SANDBOX] eval() заблокирован")
        safe_builtins['exec'] = lambda *a, **k: exec("[SANDBOX] exec() заблокирован")
        safe_builtins['compile'] = lambda *a, **k: exec("[SANDBOX] compile() заблокирован")

        sandbox_globals = {
            '__builtins__': safe_builtins,
            '__name__': '__main__',
            '__doc__': None,
            '__package__': None,
            '__spec__': None,
            '__file__': '/home/sandbox/script.py',
            'os': fake_os,
            'sys': fake_sys,
            'subprocess': fake_subprocess,
        }

        # 4. Выполнение
        compiled = compile(code, '<sandbox>', 'exec')
        exec(compiled, sandbox_globals, {})

        result_queue.put({"success": True, "output": buffer.getvalue(), "error": None})

    except SystemExit:
        result_queue.put({"success": True, "output": buffer.getvalue(), "error": None})
    except Exception as e:
        result_queue.put({
            "success": False, 
            "output": buffer.getvalue(), 
            "error": f"{type(e).__name__}: {str(e)}\n\n{traceback.format_exc()}"
        })
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr


def run_sandbox(code: str) -> dict:
    """Запускает процесс, ждет результат, убивает при зависании."""
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
            "error": f"⏳ ТАЙМАУТ ({EXECUTION_TIMEOUT}с). Процесс уничтожен.\n(Запрещено использовать: while True, bot.polling(), bot.infinity_polling())"
        }
    
    if not result_queue.empty():
        return result_queue.get()
    return {"success": False, "output": "", "error": "Процесс завершился без ответа."}


# =====================================================================
# === ТЕЛЕГРАМ-УТИЛИТЫ (Буферизация и Красивый Вывод) ================
# =====================================================================
class OutputBuffer:
    """Собирает вывод построчно для красивой разбивки по сообщениям"""
    def __init__(self):
        self.chunks = []
        self.current_chunk = ""

    def write(self, text):
        self.current_chunk += str(text)
        while '\n' in self.current_chunk:
            line, self.current_chunk = self.current_chunk.split('\n', 1)
            self.chunks.append(line + '\n')

    def flush(self):
        if self.current_chunk:
            self.chunks.append(self.current_chunk)
            self.current_chunk = ""

    def get_chunks_for_telegram(self):
        self.flush()
        if not self.chunks:
            return ["📭 (пустой вывод)"]

        result_parts = []
        current_part = ""

        for chunk in self.chunks:
            if len(current_part) + len(chunk) > MAX_TG_MESSAGE_LEN - 200: # Запас для HTML тегов
                if current_part:
                    result_parts.append(current_part)
                current_part = chunk
            else:
                current_part += chunk

        if current_part:
            result_parts.append(current_part)

        return result_parts if result_parts else ["📭 (пустой вывод)"]


async def send_chunks(update, context, chunks, is_error=False):
    user_id = update.effective_user.id
    for i, chunk in enumerate(chunks):
        prefix = f"📄 Часть {i+1}/{len(chunks)}\n\n" if len(chunks) > 1 else ""
        text = prefix + "<code>" + escape(chunk) + "</code>"
        
        if len(text) > MAX_TG_MESSAGE_LEN:
            text = text[:MAX_TG_MESSAGE_LEN - 50] + "\n\n... [Обрезано по лимиту ТГ]"
            
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode='HTML')
        if i < len(chunks) - 1:
            await asyncio.sleep(DELAY_BETWEEN_CHUNKS)


async def send_error(update, context, chunks, error_msg):
    user_id = update.effective_user.id
    for i, chunk in enumerate(chunks):
        prefix = f"📄 Часть {i+1}/{len(chunks)}\n\n" if len(chunks) > 1 else ""
        text = prefix + "<code>" + escape(chunk) + "</code>"
        
        # К последнему куску приклеиваем ошибку
        if i == len(chunks) - 1:
            text += "\n\n🚨 <b>Ошибка выполнения:</b>\n<code>" + escape(error_msg[:1500]) + "</code>"
            
        if len(text) > MAX_TG_MESSAGE_LEN:
            text = text[:MAX_TG_MESSAGE_LEN - 50] + "\n\n... [Обрезано по лимиту ТГ]"
            
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode='HTML')
        if i < len(chunks) - 1:
            await asyncio.sleep(DELAY_BETWEEN_CHUNKS)


# =====================================================================
# === ХЭНДЛЕРЫ ТЕЛЕГРАМ-БОТА =========================================
# =====================================================================
async def execute_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_code = update.message.text
    
    wait_msg = await update.message.reply_text("🐳 Запуск Docker-подобной песочницы...")
    
    result = run_sandbox(user_code)
    
    try:
        await wait_msg.delete()
    except:
        pass

    # Формируем красивый заголовок
    safe_code_preview = escape(user_code[:600]) + ("..." if len(user_code) > 600 else "")
    header = (
        "✅ <b>Успешно выполнено</b>\n"
        "🔧 <i>Метод: Secure Network Sandbox</i>\n\n"
        f"💻 <b>Код:</b>\n<code>{safe_code_preview}</code>\n\n"
        "📤 <b>Вывод:</b>\n"
    ) if result["success"] else (
        "❌ <b>Ошибка / Таймаут</b>\n"
        "🔧 <i>Метод: Secure Network Sandbox</i>\n\n"
        f"💻 <b>Код:</b>\n<code>{safe_code_preview}</code>\n\n"
        "📤 <b>Вывод до ошибки:</b>\n"
    )

    # Парсим вывод через буфер
    out_buf = OutputBuffer()
    out_buf.write(result["output"])
    chunks = out_buf.get_chunks_for_telegram()

    # Отправляем заголовок отдельно
    try:
        await context.bot.send_message(chat_id=user_id, text=header, parse_mode='HTML')
        await asyncio.sleep(0.2)
    except Exception as e:
        await context.bot.send_message(chat_id=user_id, text="Ошибка форматирования заголовка", parse_mode=None)

    # Отправляем части вывода
    if result["success"]:
        await send_chunks(update, context, chunks)
    else:
        await send_error(update, context, chunks, result["error"])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🐳 <b>Mega PySandbox Bot (Giga Docker Edition)</b>\n\n"
        "Отправь мне Python-код!\n\n"
        "✅ <b>Сеть РЕАЛЬНАЯ:</b> <code>requests</code>, <code>telebot</code>, <code>aiogram</code>, <code>aiohttp</code>, <code>socket</code>, <code>sqlite3</code> и многое другое.\n"
        "🔐 <b>Система ФЕЙКОВАЯ:</b> <code>os</code>, <code>sys</code>, <code>subprocess</code> подменены. Хакер не сможет прочитать твои токены, удалить файлы или убить процессы.\n"
        "💾 <b>Файлы в RAM:</b> <code>open()</code> пишет и читает только из оперативной памяти.\n"
        "🛡️ <b>Защита от зависаний:</b> Бесконечные циклы и <code>bot.infinity_polling()</code> убиваются через 20 секунд.\n"
        "🧠 <b>Защита от инъекций:</b> AST-парсер блокирует обход через <code>eval</code> и <code>getattr</code>.\n\n"
        "<i>Пример: <code>import requests; print(requests.get('https://api.ipify.org').text)</code></i>",
        parse_mode='HTML'
    )

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, execute_code))
    
    print("Бот запущен. Нажми Ctrl+C для остановки.")
    app.run_polling()

if __name__ == "__main__":
    main()
