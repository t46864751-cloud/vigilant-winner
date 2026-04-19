import os
import sys
import ast
import io
import time
import builtins as real_builtins
import asyncio
import traceback
from multiprocessing import Process, Queue
from unittest.mock import MagicMock
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, MessageHandler, filters, ContextTypes, 
    CommandHandler, CallbackQueryHandler
)
from html import escape

TOKEN = "8608832312:AAGKkMZTYMth41mBqiTqjhSYJypFePgtM0s"

# === КОНФИГУРАЦИЯ ===
DEFAULT_TIMEOUT = 20
MAX_TIMEOUT = 45
MIN_TIMEOUT = 5
RATE_LIMIT_SECONDS = 4 # Анти-спам: минимальная пауза между запусками
MAX_TG_MESSAGE_LEN = 4000
DELAY_BETWEEN_CHUNKS = 0.3

SAFE_ENVIRON = {
    'HOME': '/home/sandbox', 'USER': 'sandbox', 'PATH': '/usr/local/bin:/usr/bin:/bin',
    'PWD': '/home/sandbox', 'LANG': 'en_US.UTF-8', 'SHELL': '/bin/bash', 'SANDBOX': 'true'
}

ALLOWED_REAL_MODULES = {
    'math', 'random', 'datetime', 'time', 're', 'string', 'itertools', 'functools',
    'collections', 'statistics', 'typing', 'decimal', 'fractions', 'hashlib',
    'base64', 'binascii', 'inspect', 'textwrap', 'uuid', 'html', 'json', 'csv',
    'pprint', 'copy', 'warnings', 'traceback', 'types', 'enum', 'dataclasses',
    'pathlib', 'calendar', 'numbers', 'io', 'builtins', 'threading', 'logging',
    'sqlite3', 'email', 'xml', 'zipfile', 'tarfile', 'gzip', 'bz2', 'lzma',
    'secrets', 'struct', 'ctypes', 'socket', 'ssl', 'urllib', 'http', 'ftplib',
    'smtplib', 'imaplib', 'asyncio', 'concurrent', 'multiprocessing',
    'requests', 'aiohttp', 'telebot', 'pyTelegramBotAPI', 'aiogram', 'numpy',
    'pandas', 'flask', 'fastapi', 'bs4', 'lxml', 'pydantic', 'certifi',
    'charset_normalizer', 'idna', 'urllib3', 'PIL', 'pillow', 'matplotlib'
}

# =====================================================================
# === БАЗА ДАННЫХ ПОЛЬЗОВАТЕЛЕЙ (В ПАМЯТИ) ===========================
# =====================================================================
class UserProfile:
    def __init__(self, user_id):
        self.user_id = user_id
        self.timeout = DEFAULT_TIMEOUT
        self.last_run_time = 0
        self.run_count = 0

USER_DB = {} # {user_id: UserProfile}
# Временное хранилище сгенерированных файлов (чтобы кнопки скачивания работали)
# Структура: {user_id: {"files": {filename: bytes}, "map": {button_id: filename}}}
FILES_DB = {}

def get_user(user_id):
    if user_id not in USER_DB:
        USER_DB[user_id] = UserProfile(user_id)
    return USER_DB[user_id]

# =====================================================================
# === БЕЗОПАСНОСТЬ AST =================================================
# =====================================================================
class SecurityAstVisitor(ast.NodeVisitor):
    FORBIDDEN_BUILTINS = {'eval', 'exec', 'compile', 'breakpoint', '__import__'}
    def visit_Import(self, node):
        for alias in node.names:
            base_name = alias.name.split('.')[0]
            if base_name not in ALLOWED_REAL_MODULES and base_name not in ['os', 'sys', 'subprocess']:
                raise ImportError(f"🚫 Запрещен импорт: '{alias.name}'")
        self.generic_visit(node)
    def visit_ImportFrom(self, node):
        if node.module:
            base_name = node.module.split('.')[0]
            if base_name not in ALLOWED_REAL_MODULES and base_name not in ['os', 'sys', 'subprocess']:
                raise ImportError(f"🚫 Запрещен импорт из: '{node.module}'")
        self.generic_visit(node)
    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id in self.FORBIDDEN_BUILTINS:
            raise NameError(f"🚫 Вызов '{node.func.id}' заблокирован")
        self.generic_visit(node)

def check_ast_security(code: str):
    try:
        tree = ast.parse(code)
        SecurityAstVisitor().visit(tree)
    except SyntaxError as e:
        raise SyntaxError(f"Синтаксическая ошибка: {e}")

# =====================================================================
# === ФЕЙКОВАЯ ФАЙЛОВАЯ СИСТЕМА (ПОДДЕРЖКА БИНАРНЫХ ФАЙЛОВ/КАРТИНОК) =
# =====================================================================
class FakeFile:
    def __init__(self, path, fs_storage, mode='r', encoding='utf-8'):
        self.path = path
        self.storage = fs_storage
        self.mode = mode
        self.encoding = encoding
        self.closed = False
        self.position = 0
        self.is_binary = 'b' in mode
        
        if 'a' in mode:
            self.storage.setdefault(path, b"")
        elif 'w' in mode:
            self.storage[path] = b""

    def write(self, data):
        if 'r' in self.mode and '+' not in self.mode:
            raise IOError("Файл открыт только для чтения")
        
        # Поддерживаем запись как строк, так и байтов (для PIL и картинок)
        if isinstance(data, str):
            data = data.encode(self.encoding)
        elif not isinstance(data, bytes):
            data = str(data).encode(self.encoding)
            
        old_data = self.storage.get(self.path, b"")
        if 'a' in mode:
            self.storage[self.path] = old_data + data
        else:
            self.storage[self.path] = data
        return len(data)

    def read(self, size=-1):
        content = self.storage.get(self.path, b"")
        if self.is_binary:
            if size == -1:
                res = content[self.position:]
                self.position = len(content)
                return res
            result = content[self.position:self.position + size]
            self.position += len(result)
            return result
        else:
            text_content = content.decode(self.encoding, errors='replace')
            if size == -1:
                res = text_content[self.position:]
                self.position = len(text_content)
                return res
            result = text_content[self.position:self.position + size]
            self.position += len(result)
            return result

    def readline(self):
        content = self.read()
        nl_pos = content.find('\n')
        if nl_pos != -1:
            self.position -= (len(content) - nl_pos - 1)
            return content[:nl_pos+1]
        return content

    def readlines(self): return self.read().splitlines(keepends=True)
    def seek(self, pos, whence=0):
        if whence == 0: self.position = pos
        elif whence == 1: self.position += pos
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
        self.files = {} # Хранит ТОЛЬКО байты! {path: bytes}
    def open(self, path, mode='r', *args, **kwargs):
        return FakeFile(str(path), self.files, mode, kwargs.get('encoding', 'utf-8'))

# =====================================================================
# === ФЕЙКОВЫЕ СИСТЕМНЫЕ МОДУЛИ =======================================
# =====================================================================
class FakePath:
    def __init__(self, fs):
        self.fs = fs
        self.sep = '/'
        self.altsep = None
        self.extsep = '.'
        self.curdir = '.'
        self.pardir = '..'
    def join(self, *args): return os.path.join(*args)
    def exists(self, p): return str(p) in self.fs.files
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
    def getsize(self, p): return len(self.fs.files.get(str(p), b""))
    def normpath(self, p): return os.path.normpath(p)
    def isabs(self, p): return str(p).startswith('/')
    def expanduser(self, p): return p.replace('~', '/home/sandbox')
    def __getattr__(self, name): return MagicMock(return_value=f"/fake/path/{name}")

class FakeOS:
    def __init__(self, fs):
        self.fs = fs
        self.environ = SAFE_ENVIRON
        self.name = 'posix'
        self.sep = '/'
        self.linesep = '\n'
        self.pathsep = ':'
        self.devnull = '/dev/null'
        self.path = FakePath(fs)

    def system(self, command): return f"[SANDBOX BLOCKED] os.system('{command}')"
    def popen(self, *a, **k): return MagicMock(read=lambda: "[SANDBOX]", readline=lambda: "", close=lambda: None)
    def listdir(self, path='.'): return list(self.fs.files.keys())
    def mkdir(self, p, *a, **k): pass
    def makedirs(self, p, *a, **k): pass
    def remove(self, p): self.fs.files.pop(str(p), None)
    def unlink(self, p): self.remove(p)
    def rename(self, s, d):
        if str(s) in self.fs.files: self.fs.files[str(d)] = self.fs.files.pop(str(s))
    def getcwd(self): return "/home/sandbox"
    def chdir(self, p): pass
    def getpid(self): return 99999
    def getppid(self): return 1
    def getuid(self): return 1000
    def urandom(self, n):
        import random; return bytes(random.randint(0, 255) for _ in range(n))
    def getenv(self, key, default=""): return SAFE_ENVIRON.get(key, default)
    def __getattr__(self, name):
        if name in ['fork', 'kill', 'spawnl', 'spawnv', 'execv', 'execve']:
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
        self.path = ['/home/sandbox']
        self.modules = {}
        self.argv = ['sandbox.py']
    def exit(self, arg=0): raise SystemExit(f"[SANDBOX] exit({arg}) вызван")
    def __getattr__(self, name): return MagicMock(return_value=f"[SANDBOX BLOCKED sys.{name}]")

# =====================================================================
# === УМНЫЙ ИМПОРТ ====================================================
# =====================================================================
class SafeImport:
    def __init__(self, fake_os, fake_sys, fake_subprocess):
        self.fake_os = fake_os
        self.fake_sys = fake_sys
        self.fake_subprocess = fake_subprocess

    def __call__(self, name, globals=None, locals=None, fromlist=(), level=0):
        base_name = name.split('.')[0]
        if base_name == 'os': return self.fake_os
        if base_name == 'sys': return self.fake_sys
        if base_name == 'subprocess': return self.fake_subprocess
        if base_name == 'shutil': return MagicMock()
        
        if base_name in ALLOWED_REAL_MODULES:
            try:
                return real_builtins.__import__(name, globals, locals, fromlist, level)
            except ImportError as e:
                raise ImportError(f"Модуль '{name}' разрешен, но не установлен на сервере! ({e})")
        
        raise ImportError(f"🚫 [SANDBOX] Импорт модуля '{name}' заблокирован!")

# =====================================================================
# === ИЗОЛИРОВАННЫЙ РАБОЧИЙ ПРОЦЕСС ===================================
# =====================================================================
def worker_process(code: str, user_timeout: int, result_queue: Queue):
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    buffer = io.StringIO()
    start_time = time.time()

    try:
        sys.stdout = buffer
        sys.stderr = buffer

        check_ast_security(code)

        fs = FakeFileSystem()
        fake_os = FakeOS(fs)
        fake_sys = FakeSys()
        fake_subprocess = MagicMock(run=lambda *a, **k: MagicMock(returncode=0, stdout='[SANDBOX]', stderr=''))

        safe_builtins = dict(real_builtins)
        safe_builtins['open'] = fs.open
        safe_builtins['__import__'] = SafeImport(fake_os, fake_sys, fake_subprocess)
        safe_builtins['input'] = lambda prompt="": "[SANDBOX INPUT]"
        safe_builtins['eval'] = lambda *a, **k: exec("[SANDBOX] eval() заблокирован")
        safe_builtins['exec'] = lambda *a, **k: exec("[SANDBOX] exec() заблокирован")

        sandbox_globals = {
            '__builtins__': safe_builtins, '__name__': '__main__', '__doc__': None,
            'os': fake_os, 'sys': fake_sys, 'subprocess': fake_subprocess,
        }

        compiled = compile(code, '<sandbox>', 'exec')
        exec(compiled, sandbox_globals, {})

        exec_time = round(time.time() - start_time, 4)
        # Возвращаем текстовый вывод и СЛОВАРЬ СОЗДАННЫХ ФАЙЛОВ (в байтах)
        result_queue.put({
            "success": True, "output": buffer.getvalue(), "error": None,
            "files": fs.files, "exec_time": exec_time
        })

    except SystemExit:
        result_queue.put({"success": True, "output": buffer.getvalue(), "error": None, "files": {}, "exec_time": 0})
    except Exception as e:
        exec_time = round(time.time() - start_time, 4)
        result_queue.put({
            "success": False, "output": buffer.getvalue(),
            "error": f"{type(e).__name__}: {str(e)}\n\n{traceback.format_exc()}",
            "files": {}, "exec_time": exec_time
        })
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

def run_sandbox(code: str, timeout: int) -> dict:
    result_queue = Queue()
    process = Process(target=worker_process, args=(code, timeout, result_queue))
    
    process.start()
    process.join(timeout=timeout)
    
    if process.is_alive():
        process.terminate()
        process.join(timeout=1)
        if process.is_alive():
            process.kill()
            process.join()
        return {
            "success": False, "output": "", "files": {},
            "error": f"⏳ ТАЙМАУТ ({timeout}с). Процесс уничтожен.", "exec_time": timeout
        }
    
    if not result_queue.empty():
        return result_queue.get()
    return {"success": False, "output": "", "error": "Процесс упал без объяснения.", "files": {}, "exec_time": 0}

# =====================================================================
# === УТИЛИТЫ ОТПРАВКИ СООБЩЕНИЙ ======================================
# =====================================================================
class OutputBuffer:
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
        if not self.chunks: return ["📭 (пустой вывод)"]
        result_parts, current_part = [], ""
        for chunk in self.chunks:
            if len(current_part) + len(chunk) > MAX_TG_MESSAGE_LEN - 200:
                if current_part: result_parts.append(current_part)
                current_part = chunk
            else:
                current_part += chunk
        if current_part: result_parts.append(current_part)
        return result_parts if result_parts else ["📭 (пустой вывод)"]

async def send_text_chunks(user_id, context, chunks, error_msg=None):
    for i, chunk in enumerate(chunks):
        prefix = f"📄 Часть {i+1}/{len(chunks)}\n\n" if len(chunks) > 1 else ""
        text = prefix + "<code>" + escape(chunk) + "</code>"
        
        if error_msg and i == len(chunks) - 1:
            text += "\n\n🚨 <b>Ошибка:</b>\n<code>" + escape(error_msg[:1500]) + "</code>"
            
        if len(text) > MAX_TG_MESSAGE_LEN:
            text = text[:MAX_TG_MESSAGE_LEN - 50] + "\n\n... [Обрезано]"
            
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode='HTML')
        if i < len(chunks) - 1:
            await asyncio.sleep(DELAY_BETWEEN_CHUNKS)

# =====================================================================
# === ГЛАВНЫЕ ХЭНДЛЕРЫ ТЕЛЕГРАМ-БОТА =================================
# =====================================================================
async def execute_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    user_code = update.message.text
    
    # 1. АНТИ-СПАМ ЗАЩИТА
    current_time = time.time()
    if current_time - user.last_run_time < RATE_LIMIT_SECONDS:
        wait_sec = round(RATE_LIMIT_SECONDS - (current_time - user.last_run_time), 1)
        await update.message.reply_text(f"⏳ Анти-спам. Подождите {wait_sec} сек. перед следующим запуском.")
        return
    user.last_run_time = current_time
    user.run_count += 1

    wait_msg = await update.message.reply_text("🐳 Запуск изолированного контейнера...")
    result = run_sandbox(user_code, user.timeout)
    
    try: await wait_msg.delete()
    except: pass

    # 2. ОБРАБОТКА СГЕНЕРИРОВАННЫХ ФАЙЛОВ (Кнопки скачивания)
    reply_markup = None
    if result.get("files"):
        # Сохраняем файлы в глобальную RAM-базу для кнопок
        FILES_DB[user_id] = {"files": result["files"], "map": {}}
        buttons = []
        for idx, filename in enumerate(result["files"].keys()):
            btn_id = f"dl_{idx}"
            FILES_DB[user_id]["map"][btn_id] = filename
            # Обрезаем длинные имена для кнопки
            display_name = filename if len(filename) <= 30 else "..." + filename[-27:]
            buttons.append([InlineKeyboardButton(f"📂 Скачать {display_name}", callback_data=btn_id)])
        
        reply_markup = InlineKeyboardMarkup(buttons)

    # 3. ФОРМИРОВАНИЕ ТЕКСТА ОТВЕТА
    safe_code_preview = escape(user_code[:500]) + ("..." if len(user_code) > 500 else "")
    status = "✅ Успешно" if result["success"] else "❌ Ошибка/Таймаут"
    time_info = f"⏱ За {result['exec_time']}с | Лимит: {user.timeout}с"
    
    header = (
        f"<b>{status}</b> | {time_info}\n"
        "─────────────────────\n"
        f"💻 <b>Код:</b>\n<code>{safe_code_preview}</code>\n\n"
        "─────────────────────\n"
        "📤 <b>Вывод:</b>\n"
    )

    out_buf = OutputBuffer()
    out_buf.write(result["output"])
    chunks = out_buf.get_chunks_for_telegram()

    try:
        await context.bot.send_message(chat_id=user_id, text=header, parse_mode='HTML')
        await asyncio.sleep(0.2)
        await send_text_chunks(user_id, context, chunks, None if result["success"] else result["error"])
        
        # Если есть файлы, отправляем сообщение с кнопками
        if reply_markup:
            await context.bot.send_message(
                chat_id=user_id, 
                text="📁 <b>Скрипт создал файлы. Нажмите для скачивания:</b>", 
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
    except Exception as e:
        await context.bot.send_message(chat_id=user_id, text=f"Ошибка рендера: {e}")

# =====================================================================
# === ИНТЕРФЕЙС: ГЛАВНОЕ МЕНЮ, НАСТРОЙКИ, ПРОФИЛЬ ====================
# =====================================================================
def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("⚙️ Настройки", callback_data="menu_settings"),
         InlineKeyboardButton("👤 Мой профиль", callback_data="menu_profile")],
        [InlineKeyboardButton("❓ Как пользоваться", callback_data="menu_help")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🐳 <b>Mega PySandbox Bot</b>\n\n"
        "Отправь мне Python-код, и я выполню его в безопасном контейнере с доступом к интернету.\n"
        "Ты можешь создавать файлы, картинки (PIL) и скачивать их!"
    )
    await update.message.reply_text(text, reply_markup=get_main_menu(), parse_mode='HTML')

async def menu_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() # Убираем часики на кнопке
    user_id = query.from_user.id
    user = get_user(user_id)
    data = query.data

    if data == "menu_settings":
        kb = [
            [InlineKeyboardButton(f"⏱ Текущий таймаут: {user.timeout} сек", callback_data="none")],
            [
                InlineKeyboardButton("➖ 5 сек", callback_data="set_timeout_-5"),
                InlineKeyboardButton("➕ 5 сек", callback_data="set_timeout_+5")
            ],
            [InlineKeyboardButton("🔙 Назад", callback_data="menu_back")]
        ]
        text = "⚙️ <b>Настройки контейнера</b>\n\nУстанавливай таймаут выполнения кода (макс. 45 сек). Полезно для сложных парсеров."
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

    elif data.startswith("set_timeout_"):
        change = int(data.split("_")[2])
        new_timeout = user.timeout + change
        new_timeout = max(MIN_TIMEOUT, min(MAX_TIMEOUT, new_timeout))
        user.timeout = new_timeout
        
        # Обновляем сообщение с новыми значениями
        kb = [
            [InlineKeyboardButton(f"⏱ Текущий таймаут: {user.timeout} сек", callback_data="none")],
            [
                InlineKeyboardButton("➖ 5 сек", callback_data="set_timeout_-5"),
                InlineKeyboardButton("➕ 5 сек", callback_data="set_timeout_+5")
            ],
            [InlineKeyboardButton("🔙 Назад", callback_data="menu_back")]
        ]
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))

    elif data == "menu_profile":
        username = query.from_user.username or "Не задан"
        text = (
            "👤 <b>Твой профиль в песочнице</b>\n\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            f"📛 Юзернейм: @{escape(username)}\n"
            f"⏱ Личный таймаут: {user.timeout} сек\n"
            f"🚀 Запусков выполнено: {user.run_count}\n"
            f"📁 Файлов в буфере: {len(FILES_DB.get(user_id, {}).get('files', {}))}"
        )
        kb = [[InlineKeyboardButton("🔙 Назад", callback_data="menu_back")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

    elif data == "menu_help":
        text = (
            "❓ <b>Инструкция</b>\n\n"
            "1. Просто пиши код в чат.\n"
            "2. Сеть работает: <code>import requests</code>.\n"
            "3. Система фейковая: <code>os.system()</code> заблокировано.\n"
            "4. Файлы пишутся в ОЗУ:\n"
            "   <code>f = open('test.txt', 'w'); f.write('Hi')</code>\n"
            "5. Если скрипт создает файл (или картинку через PIL), под выводом появится кнопка для скачивания!\n"
            "6. Не используй <code>while True</code> или бот зависнет и упадет по таймауту."
        )
        kb = [[InlineKeyboardButton("🔙 Назад", callback_data="menu_back")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

    elif data == "menu_back":
        text = (
            "🐳 <b>Mega PySandbox Bot</b>\n\n"
            "Отправь мне Python-код, и я выполню его в безопасном контейнере с доступом к интернету."
        )
        await query.edit_message_text(text, reply_markup=get_main_menu(), parse_mode='HTML')

    elif data.startswith("dl_"):
        # ОБРАБОТКА СКАЧИВАНИЯ ФАЙЛОВ
        if user_id not in FILES_DB:
            await query.answer("Файлы устарели. Запусти скрипт заново.", show_alert=True)
            return
            
        file_map = FILES_DB[user_id].get("map", {})
        files_data = FILES_DB[user_id].get("files", {})
        
        filename = file_map.get(data)
        if not filename or filename not in files_data:
            await query.answer("Файл не найден.", show_alert=True)
            return
            
        file_bytes = files_data[filename]
        
        # Уведомляем пользователя
        await query.answer("📎 Отправляю файл...", show_alert=False)
        
        try:
            # Определяем, картинка это или документ
            ext = filename.lower().split('.')[-1] if '.' in filename else ""
            is_image = ext in ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp']
            
            if is_image:
                await context.bot.send_photo(
                    chat_id=user_id, 
                    photo=io.BytesIO(file_bytes), 
                    caption=f"🖼 Изображение: {escape(filename)}"
                )
            else:
                await context.bot.send_document(
                    chat_id=user_id, 
                    document=io.BytesIO(file_bytes), 
                    filename=filename
                )
        except Exception as e:
            await context.bot.send_message(chat_id=user_id, text=f"❌ Ошибка отправки файла: {e}")

# =====================================================================
# === ЗАПУСК ===========================================================
# =====================================================================
def main():
    app = Application.builder().token(TOKEN).build()
    
    # Роутинг
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(menu_callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, execute_code))
    
    print("🐳 Mega PySandbox Bot запущен. Нажми Ctrl+C для остановки.")
    app.run_polling()

if __name__ == "__main__":
    main()
