import os
import sys
import ast
import io
import re
import time
import builtins as real_builtins
import asyncio
import traceback
from datetime import datetime
from multiprocessing import Process, Queue
from unittest.mock import MagicMock
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, MessageHandler, filters, ContextTypes, 
    CommandHandler, CallbackQueryHandler
)
from html import escape

TOKEN = "8608832312:AAGKkMZTYMth41mBqiTqjhSYJypFePgtM0s"

# =====================================================================
# === КОНФИГУРАЦИЯ ПЕСОЧНИЦЫ =========================================
# =====================================================================
DEFAULT_TIMEOUT = 20
MAX_TIMEOUT = 45
MIN_TIMEOUT = 5
RATE_LIMIT_SECONDS = 4 
MAX_TG_MESSAGE_LEN = 4000
DELAY_BETWEEN_CHUNKS = 0.3 
MAX_OUTPUT_BYTES = 500000 # Жесткий лимит вывода в байтах, чтобы не уронить сервер от print("A"*999999)

SAFE_ENVIRON = {
    'HOME': '/home/sandbox',
    'USER': 'sandbox',
    'PATH': '/usr/local/bin:/usr/bin:/bin',
    'PWD': '/home/sandbox',
    'LANG': 'en_US.UTF-8',
    'SHELL': '/bin/bash',
    'SANDBOX': 'true'
}

ALLOWED_REAL_MODULES = {
    # Стандартная библиотека (Вычисления и данные)
    'math', 'random', 'datetime', 'time', 're', 'string', 'itertools', 'functools',
    'collections', 'statistics', 'typing', 'decimal', 'fractions', 'hashlib',
    'base64', 'binascii', 'inspect', 'textwrap', 'uuid', 'html', 'json', 'csv',
    'pprint', 'copy', 'warnings', 'traceback', 'types', 'enum', 'dataclasses',
    'pathlib', 'calendar', 'numbers', 'io', 'builtins', 'threading', 'logging',
    'sqlite3', 'email', 'xml', 'zipfile', 'tarfile', 'gzip', 'bz2', 'lzma',
    'secrets', 'struct', 'ctypes',
    
    # Сетевые (Настоящие сокеты и запросы)
    'socket', 'ssl', 'urllib', 'http', 'ftplib', 'smtplib', 'imaplib',
    
    # Асинхронность
    'asyncio', 'concurrent', 'multiprocessing',
    
    # Сторонние (Добавляй сюда то, что установлено через pip на сервере)
    'requests', 'aiohttp', 'telebot', 'pyTelegramBotAPI',
    'aiogram', 'numpy', 'pandas', 'flask', 'fastapi', 'bs4', 'lxml',
    'pydantic', 'certifi', 'charset_normalizer', 'idna', 'urllib3',
    
    # Работа с медиа (Картинки, графики)
    'PIL', 'pillow', 'matplotlib'
}

# =====================================================================
# === БАЗА ДАННЫХ ПОЛЬЗОВАТЕЛЕЙ (Детальная статистика) ================
# =====================================================================
class UserProfile:
    """Класс для хранения детальной информации о пользователе и его настройках"""
    def __init__(self, user_id):
        self.user_id = user_id
        self.timeout = DEFAULT_TIMEOUT
        self.last_run_time = 0
        self.run_count = 0
        self.silent_mode = False # Тихий режим (вкл/выкл)
        
        # Дополнительная статистика для профиля
        self.first_seen = datetime.now().strftime("%d.%m.%Y %H:%M")
        self.total_chars_typed = 0
        self.longest_code = 0
        self.last_error_type = "Нет ошибок"

USER_DB = {} # Храним профили: {user_id: UserProfile}
FILES_DB = {} # Временное хранилище сгенерированных файлов

def get_user(user_id):
    """Получает профиль пользователя, создает новый если его не было"""
    if user_id not in USER_DB:
        USER_DB[user_id] = UserProfile(user_id)
    return USER_DB[user_id]

# =====================================================================
# === УМНЫЕ ОШИБКИ (Огромный класс-анализатор) =======================
# =====================================================================
class SmartErrorAnalyzer:
    """
    Класс анализирует текст ошибки (Traceback) и сопоставляет его с известными шаблонами,
    возвращая человеческим языком понятную подсказку, почему код не сработал.
    """
    
    def analyze(self, error_text):
        """Главная точка входа. Возвращает строку с подсказкой или None"""
        if not error_text:
            return None
            
        text_lower = error_text.lower()
        
        # --- СИНТАКСИС ---
        if "syntaxerror" in text_lower:
            if "unexpected eof" in text_lower or "eof while parsing" in text_lower:
                return "💡 <b>Подсказка:</b> Ошибка синтаксиса. Скорее всего, ты забыл закрыть скобку <code>)</code>, квадратную скобку <code>]</code> или кавычку <code>\"</code> в конце кода."
            if "invalid syntax" in text_lower:
                return "💡 <b>Подсказка:</b> Недопустимый синтаксис. Возможно, лишняя или отсутствующая запятая, неправильно написано ключевое слово (например, <code>elso</code> вместо <code>else</code>), или попытка использовать <code>print</code> как в Python 2."
            if "eol while scanning string literal" in text_lower:
                return "💡 <b>Подсказка:</b> Строка не закрыта. Ты забыл поставить закрывающую кавычку <code>'</code> или <code>\"</code>."

        # --- ОТСТУПЫ ---
        if "indentationerror" in text_lower:
            if "expected an indented block" in text_lower:
                return "💡 <b>Подсказка:</b> Ошибка отступов. После двоеточия <code>:</code> на следующей строке должен быть код (отступ). Нельзя оставить строку пустой, поставь <code>pass</code>."
            if "unindent does not match" in text_lower:
                return "💡 <b>Подсказка:</b> Несовпадение отступов. Проверь, чтобы в одном блоке кода были одинаковые пробелы (не смешивай пробелы и табуляции)."

        # --- ПЕРЕМЕННЫЕ ---
        if "nameerror" in text_lower and "is not defined" in text_lower:
            match = re.search(r"'(.+?)' is not defined", error_text)
            var_name = match.group(1) if match else "переменная"
            return f"💡 <b>Подсказка:</b> Имя <code>{escape(var_name)}</code> не найдено. Возможно, ты забыл её создать (например, <code>x = 10</code>), опечатался в названии, или забыл сделать <code>import</code>."

        # --- ТИПЫ ДАННЫХ ---
        if "typeerror" in text_lower:
            if "not subscriptable" in text_lower:
                match = re.search(r"'(.+?)' object is not subscriptable", error_text)
                obj_type = match.group(1) if match else "объект"
                return f"💡 <b>Подсказка:</b> Нельзя обратиться к объекту типа <code>{escape(obj_type)}</code> по индексу. Числа нельзя индексировать (нельзя сделать <code>123[0]</code>). Используй преобразование в строку: <code>str(123)[0]</code> или в список."
            if "unsupported operand type" in text_lower:
                return "💡 <b>Подсказка:</b> Нельзя выполнить математическую операцию с разными типами данных (например, сложить строку и число <code>'2' + 2</code>). Используй <code>int()</code> или <code>str()</code> для приведения к одному типу."
            if "missing 1 required positional argument" in text_lower:
                match = re.search(r"'(.+?)'", error_text)
                arg_name = match.group(1) if match else "аргумент"
                return f"💡 <b>Подсказка:</b> Функция или метод требует обязательный аргумент <code>{escape(arg_name)}</code>, но ты вызвал его без него."
            if "can only concatenate str" in text_lower:
                return "💡 <b>Подсказка:</b> Можно складывать только строки со строками (или списки со списками). Проверь, все ли элементы одного типа."
            if "object is not callable" in text_lower:
                match = re.search(r"'(.+?)' object is not callable", error_text)
                obj_name = match.group(1) if match else "объект"
                return f"💡 <b>Подсказка:</b> Ты пытаешься вызвать <code>{escape(obj_name)}</code> как функцию (со скобками <code>()</code>), но это переменная. Убери скобки или проверь имя функции."

        # --- МОДУЛИ ---
        if "modulenotfounderror" in text_lower:
            match = re.search(r"'(.+?)'", error_text)
            mod_name = match.group(1) if match else "модуль"
            return f"💡 <b>Подсказка:</b> Модуль <code>{escape(mod_name)}</code> не найден. Либо он жестко заблокирован песочницей ради безопасности (например, <code>os</code>, <code>sys</code>), либо он просто не установлен на сервере."
        if "importerror" in text_lower:
            if "cannot import name" in text_lower:
                return "💡 <b>Подсказка:</b> Ты пытаешься импортировать функцию, которой не существует в этом модуле. Проверь правильность написания функции."
            if "no module named" in text_lower:
                match = re.search(r"'(.+?)'", error_text)
                mod_name = match.group(1) if match else "модуль"
                return f"💡 <b>Подсказка:</b> Не удалось найти часть модуля <code>{escape(mod_name)}</code>."

        # --- МАТЕМАТИКА ---
        if "zerodivisionerror" in text_lower:
            return "💡 <b>Подсказка:</b> Деление на ноль! Проверь знаменатель в математическом выражении или убедись, что список не пуст, если ты делишь на его длину (<code>len(lst)</code>)."

        # --- ИНДЕКСЫ ---
        if "indexerror" in text_lower:
            if "list index out of range" in text_lower:
                return "💡 <b>Подсказка:</b> Индекс списка вне диапазона. Ты пытаешься получить элемент по номеру, которого нет в списке (например, в списке из 3 элементов нет 10-го)."
            if "string index out of range" in text_lower:
                return "💡 <b>Подсказка:</b> Индекс строки вне диапазона. Строка короче, чем номер символа, который ты запрашиваешь."

        # --- СЛОВАРЯ ---
        if "keyerror" in text_lower:
            match = re.search(r"'(.+?)'", error_text)
            key_name = match.group(1) if match else "ключ"
            return f"💡 <b>Подсказка:</b> Ключа <code>{escape(key_name)}</code> нет в словаре. Чтобы избежать ошибки и получить None, используй метод <code>.get('{escape(key_name)}')</code>."

        # --- АТРИБУТЫ ---
        if "attributeerror" in text_lower:
            if "object has no attribute" in text_lower:
                match = re.search(r"'(.+?)' object has no attribute '(.+?)'", error_text)
                if match:
                    obj_type = match.group(1)
                    attr_name = match.group(2)
                    return f"💡 <b>Подсказка:</b> У объекта типа <code>{escape(obj_type)}</code> нет свойства или метода <code>{escape(attr_name)}</code>. Возможно, опечатка в названии или неправильный тип данных."
                return "💡 <b>Подсказка:</b> У этого объекта нет такого метода или свойства. Проверь тип объекта (возможно, это не тот класс, который ты думаешь)."
            if "'nonetype' object has no attribute" in text_lower:
                return "💡 <b>Подсказка:</b> Ты пытаешься вызвать метод у <code>None</code> (пустоты). Скорее всего, какая-то функция выше вернула <code>None</code> вместо объекта, а ты этого не заметил."

        # --- ФАЙЛЫ ---
        if "filenotfounderror" in text_lower:
            return "💡 <b>Подсказка:</b> Файл не найден. В песочнице нет этого файла. Сначала создай его через <code>open('имя', 'w')</code>."

        # --- ПРОЧЕЕ ---
        if "recursionerror" in text_lower:
            return "💡 <b>Подсказка:</b> Рекурсия ушла в бесконечный цикл. Функция вызывает саму себя слишком много раз без условия выхода (базового случая)."
            
        if "valueerror" in text_lower:
            if "invalid literal for int()" in text_lower:
                return "💡 <b>Подсказка:</b> Невозможно преобразовать строку в число через <code>int()</code>. Строка содержит буквы или спецсимволы, а не цифры."
            if "math domain error" in text_lower:
                return "💡 <b>Подсказка:</b> Ошибка математической области. Скорее всего, ты пытаешься извлечь корень из отрицательного числа или вычислить логарифм от нуля/отрицательного числа."
                
        if "timeout" in text_lower:
            return "💡 <b>Подсказка:</b> Скрипт выполнялся слишком долго и был принудительно убит системой. Избегай бесконечных циклов <code>while True</code> без <code>break</code> или долгих запросов к неработающим сайтам."

        return None

def get_time_emoji(exec_time):
    """Возвращает эмоциональный статус времени выполнения"""
    if exec_time < 0.05:
        return f"⚡ Молниеносно ({exec_time}с)"
    elif exec_time < 0.5:
        return f"🏃 Очень быстро ({exec_time}с)"
    elif exec_time < 3.0:
        return f"🐢 Медленно ({exec_time}с)"
    else:
        return f"🐌 Тяжелый скрипт ({exec_time}с)"

# Глобальный экземпляр анализатора ошибок
ErrorAnalyzer = SmartErrorAnalyzer()

# =====================================================================
# === БЕЗОПАСНОСТЬ AST (Защита от хитрых хакеров) ====================
# =====================================================================
class SecurityAstVisitor(ast.NodeVisitor):
    """
    Парсит дерево кода ДО выполнения.
    Блокирует: import os, exec(''), compile и т.д.
    ВНИМАНИЕ: 'eval' убран из блокировок, чтобы он работал через SafeEval!
    """
    FORBIDDEN_BUILTINS = {'exec', 'compile', 'breakpoint', '__import__'}
    
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
            raise NameError(f"🚫 Вызов '{node.func.id}' заблокирован в песочнице")
        self.generic_visit(node)

def check_ast_security(code):
    """Запускает проверку дерева кода"""
    try:
        tree = ast.parse(code)
        SecurityAstVisitor().visit(tree)
    except SyntaxError as e:
        raise SyntaxError(f"Синтаксическая ошибка: {e}")

# =====================================================================
# === ФЕЙКОВАЯ ФАЙЛОВАЯ СИСТЕМА (Всё в оперативной памяти) ===========
# =====================================================================
class FakeFile:
    """Максимально полная эмуляция файлового объекта для песочницы"""
    def __init__(self, path, fs_storage, mode='r', encoding='utf-8'):
        self.path = path
        self.storage = fs_storage
        self.mode = mode
        self.encoding = encoding
        self.closed = False
        self.position = 0
        self.is_binary = 'b' in self.mode
        
        if 'a' in self.mode:
            self.storage.setdefault(path, b"")
        elif 'w' in self.mode:
            self.storage[path] = b""

    def write(self, data):
        if 'r' in self.mode and '+' not in self.mode:
            raise IOError("Файл открыт только для чтения")
        if isinstance(data, str):
            data = data.encode(self.encoding)
        elif not isinstance(data, bytes):
            data = str(data).encode(self.encoding)
        old_data = self.storage.get(self.path, b"")
        if 'a' in self.mode:
            self.storage[self.path] = old_data + data
        else:
            self.storage[self.path] = data
        return len(data)

    def writelines(self, lines):
        """Записывает список строк в файл"""
        for line in lines:
            self.write(line)

    def read(self, size=-1):
        content = self.storage.get(self.path, b"")
        if self.is_binary:
            if size == -1: 
                res = content[self.position:]; self.position = len(content); return res
            result = content[self.position:self.position + size]; self.position += len(result); return result
        else:
            text_content = content.decode(self.encoding, errors='replace')
            if size == -1: 
                res = text_content[self.position:]; self.position = len(text_content); return res
            result = text_content[self.position:self.position + size]; self.position += len(result); return result

    def readline(self):
        content = self.read()
        nl_pos = content.find('\n')
        if nl_pos != -1: 
            self.position -= (len(content) - nl_pos - 1)
            return content[:nl_pos+1]
        return content

    def readlines(self): 
        return self.read().splitlines(keepends=True)
        
    def seek(self, pos, whence=0):
        if whence == 0: self.position = pos
        elif whence == 1: self.position += pos
        elif whence == 2: self.position = len(self.storage.get(self.path, b"")) + pos
        
    def tell(self): 
        return self.position
        
    def truncate(self, size=None):
        """Обрезает файл"""
        content = self.storage.get(self.path, b"")
        if size is not None:
            self.storage[self.path] = content[:size]
        else:
            self.storage[self.path] = content[:self.position]
            
    def flush(self): 
        pass
        
    def close(self): 
        self.closed = True
        
    def fileno(self): 
        return -1 # Заглушка, т.к. реального дескриптора нет
        
    def isatty(self): 
        return False
        
    def readable(self): 
        return 'r' in self.mode
        
    def writable(self): 
        return 'w' in self.mode or 'a' in self.mode or '+' in self.mode
        
    def seekable(self): 
        return True
        
    def __iter__(self):
        while True:
            line = self.readline()
            if not line: break
            yield line
            
    def __enter__(self): 
        return self
        
    def __exit__(self, *args): 
        self.close()

class FakeFileSystem:
    def __init__(self): 
        self.files = {} # Хранит ТОЛЬКО байты! {path: bytes}
        
    def open(self, path, mode='r', *args, **kwargs): 
        return FakeFile(str(path), self.files, mode, kwargs.get('encoding', 'utf-8'))

# =====================================================================
# === ФЕЙКОВЫЕ СИСТЕМНЫЕ МОДУЛИ (Docker-эмуляция, РАЗВЕРНУТО) ======
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
        self.devnull = '/dev/null'
        
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
    def normcase(self, p): return str(p).lower()
    def relpath(self, path, start=None): return self.abspath(path)
    def commonprefix(self, list): return ""
    def commonpath(self, list): return "/"
    def islink(self, p): return False
    def ismount(self, p): return p == '/'
    def lexists(self, p): return self.exists(p)
    def samefile(self, p1, p2): return False
    def samestat(self, s1, s2): return False
    def __getattr__(self, name): return MagicMock(return_value=f"/fake/path/{name}")


class FakeOS:
    """Подмена системного модуля os"""
    def __init__(self, fs):
        self.fs = fs
        self.environ = SAFE_ENVIRON
        self.name = 'posix'
        self.sep = '/'
        self.linesep = '\n'
        self.pathsep = ':'
        self.devnull = '/dev/null'
        self.path = FakePath(fs)
        self.curdir = '.'
        self.pardir = '..'

    def system(self, command): return f"[SANDBOX BLOCKED] os.system('{command}')"
    def popen(self, *a, **k): return MagicMock(read=lambda: "[SANDBOX]", readline=lambda: "", close=lambda: None)
    def listdir(self, path='.'): return list(self.fs.files.keys())
    def mkdir(self, p, *a, **k): pass
    def makedirs(self, p, *a, **k): pass
    def remove(self, p): self.fs.files.pop(str(p), None)
    def unlink(self, p): self.remove(p)
    def rename(self, s, d):
        if str(s) in self.fs.files: self.fs.files[str(d)] = self.fs.files.pop(str(s))
    def replace(self, s, d): self.rename(s, d)
    def stat(self, p): return MagicMock(st_size=1024, st_mtime=time.time())
    def lstat(self, p): return self.stat(p)
    def access(self, p, mode): return True
    def chmod(self, p, mode): pass
    def chown(self, p, uid, gid): pass
    def getcwd(self): return "/home/sandbox"
    def chdir(self, p): pass
    def getpid(self): return 99999
    def getppid(self): return 1
    def getuid(self): return 1000
    def getgid(self): return 1000
    def geteuid(self): return 1000
    def getegid(self): return 1000
    def getpgid(self, pid): return 0
    def getsid(self, pid): return 0
    def getlogin(self): return "sandbox"
    def cpu_count(self): return 2
    def urandom(self, n):
        import random; return bytes(random.randint(0, 255) for _ in range(n))
    def getenv(self, key, default=""): return SAFE_ENVIRON.get(key, default)
    def putenv(self, key, value): pass
    def unsetenv(self, key): pass
    def symlink(self, src, dst): raise OSError("[SANDBOX] symlinks blocked")
    def readlink(self, p): return ""
    def scandir(self, path='.'): return []
    def uname(self): return MagicMock(sysname="Linux", nodename="sandbox", release="5.15.0", version="#1", machine="x86_64")
    
    # Критически опасные системные вызовы - заглушки
    def fork(self): raise OSError("[SANDBOX BLOCKED] fork() is not allowed")
    def kill(self, pid, sig): raise OSError("[SANDBOX BLOCKED] kill() is not allowed")
    def execv(self, path, args): raise OSError("[SANDBOX BLOCKED] execv() is not allowed")
    def execve(self, path, args, env): raise OSError("[SANDBOX BLOCKED] execve() is not allowed")
    def spawnl(self, mode, file, *args): raise OSError("[SANDBOX BLOCKED] spawnl() is not allowed")
    def spawnv(self, mode, file, args): raise OSError("[SANDBOX BLOCKED] spawnv() is not allowed")
    def setsid(self): raise OSError("[SANDBOX BLOCKED] setsid() is not allowed")
    def setuid(self, uid): raise OSError("[SANDBOX BLOCKED] setuid() is not allowed")
    def setgid(self, gid): raise OSError("[SANDBOX BLOCKED] setgid() is not allowed")
    
    def __getattr__(self, name):
        return MagicMock(return_value=f"[SANDBOX BLOCKED os.{name}]")


class FakeSys:
    """Подмена модуля sys"""
    def __init__(self):
        self.stdout = MagicMock(write=lambda x: None, flush=lambda: None)
        self.stderr = MagicMock(write=lambda x: None, flush=lambda: None)
        self.stdin = MagicMock(read=lambda: "", readline=lambda: "[SANDBOX INPUT]\n")
        self.version = "3.11.0 (Secure Sandbox Edition)"
        self.version_info = (3, 11, 0, 'final', 0)
        self.platform = "linux"
        self.byteorder = "little"
        self.maxsize = 9223372036854775807
        self.path = ['/home/sandbox']
        self.modules = {}
        self.argv = ['sandbox.py']
        self.executable = '/usr/bin/python3'
        self.prefix = '/usr'
        self.exec_prefix = '/usr'
        self.base_prefix = '/usr'
        self.platlibdir = 'lib'
        
    def exit(self, arg=0): raise SystemExit(f"[SANDBOX] exit({arg}) вызван")
    
    def __getattr__(self, name): return MagicMock(return_value=f"[SANDBOX BLOCKED sys.{name}]")

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
        
        # Подмена системных модулей (Безопасность)
        if base_name == 'os': return self.fake_os
        if base_name == 'sys': return self.fake_sys
        if base_name == 'subprocess': return self.fake_subprocess
        if base_name == 'shutil': return MagicMock()
        
        # Выдача настоящих модулей (Сеть и Вычисления)
        if base_name in ALLOWED_REAL_MODULES:
            try:
                return real_builtins.__import__(name, globals, locals, fromlist, level)
            except ImportError as e:
                raise ImportError(f"Модуль '{name}' разрешен, но не установлен на сервере! ({e})")
        
        # Блокировка всего остального
        raise ImportError(f"🚫 [SANDBOX] Импорт модуля '{name}' жестко заблокирован!")

# =====================================================================
# === БЕЗОПАСНЫЙ EVAL ==================================================
# =====================================================================
class SafeEval:
    """
    Эмуляция встроенного eval(). Почти настоящая, но без "ядерной кнопки".
    Разрешает математику, len(), int(), str(), list(), dict() и переменные из кода.
    Жестко блокирует: __import__, getattr, type, vars, open.
    """
    def __call__(self, expression, globals=None, locals=None):
        # Формируем словарь дозволенных встроенных функций для eval
        safe_eval_builtins = {
            # Математика и логика
            'abs': abs, 'all': all, 'any': any, 'bin': bin, 'bool': bool,
            'chr': chr, 'complex': complex, 'divmod': divmod, 'float': float,
            'hash': hash, 'hex': hex, 'int': int, 'max': max, 'min': min,
            'oct': oct, 'ord': ord, 'pow': pow, 'round': round,
            
            # Структуры данных
            'bytearray': bytearray, 'bytes': bytes, 'dict': dict, 'enumerate': enumerate,
            'filter': filter, 'frozenset': frozenset, 'iter': iter, 'len': len,
            'list': list, 'map': map, 'range': range, 'reversed': reversed,
            'set': set, 'slice': slice, 'sorted': sorted, 'str': str, 'sum': sum,
            'tuple': tuple, 'zip': zip,
            
            # Строки и форматирование
            'ascii': ascii, 'format': format, 'repr': repr,
            
            # Константы
            'True': True, 'False': False, 'None': None,
            
            # Функции для проверок (безопасные)
            'isinstance': isinstance, 'issubclass': issubclass, 'callable': callable,
        }
        
        # Формируем безопасный словарь глобальных переменных
        safe_globals = {'__builtins__': safe_eval_builtins}
        
        if globals:
            # Пробрасываем переменные, которые юзер создал в коде (чтобы x=5 работало внутри eval)
            # Но отсекаем системные объекты
            for k, v in globals.items():
                if not k.startswith('__') and k not in ['os', 'sys', 'subprocess', 'open']:
                    safe_globals[k] = v

        safe_locals = locals if locals else {}

        # Выполняем в защищенном контексте
        try:
            return eval(expression, safe_globals, safe_locals)
        except Exception as e:
            return f"[SAFE EVAL ERROR] {type(e).__name__}: {e}"
    
    def __repr__(self):
        return "<built-in function eval>"

# =====================================================================
# === ИЗОЛИРОВАННЫЙ РАБОЧИЙ ПРОЦЕСС (Ядро Песочницы) ================
# =====================================================================
def worker_process(code: str, user_timeout: int, result_queue: Queue):
    """
    Запускается в ОТДЕЛЬНОМ процессе ОС.
    Имеет доступ к сети, но сидит в клетке из фейковых os/sys/open.
    """
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    buffer = io.StringIO()
    start_time = time.time()

    try:
        sys.stdout = buffer
        sys.stderr = buffer

        # Защита для Docker: принудительно отключаем GUI для matplotlib
        try:
            import matplotlib
            matplotlib.use('Agg')
        except ImportError:
            pass

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
        safe_builtins = dict(real_builtins.__dict__)
        safe_builtins['open'] = fs.open
        safe_builtins['__import__'] = SafeImport(fake_os, fake_sys, fake_subprocess)
        safe_builtins['input'] = lambda prompt="": "[SANDBOX INPUT]"
        
        # Подключаем наш кастомный SafeEval вместо настоящего
        safe_builtins['eval'] = SafeEval()
        
        # Жесткая блокировка остальных опасных вызовов
        safe_builtins['exec'] = lambda *a, **k: exec("[SANDBOX] exec() заблокирован")
        safe_builtins['compile'] = lambda *a, **k: exec("[SANDBOX] compile() заблокирован")

        sandbox_globals = {
            '__builtins__': safe_builtins, '__name__': '__main__', '__doc__': None,
            'os': fake_os, 'sys': fake_sys, 'subprocess': fake_subprocess,
        }

        # 4. Выполнение
        compiled = compile(code, '<sandbox>', 'exec')
        exec(compiled, sandbox_globals, {})

        exec_time = round(time.time() - start_time, 4)
        
        # БЕЗОПАСНОСТЬ: Обрезаем вывод если юзер попытался забить память
        out_text = buffer.getvalue()
        if len(out_text.encode('utf-8')) > MAX_OUTPUT_BYTES:
            out_text = out_text[:MAX_OUTPUT_BYTES//2] + "\n\n⚠️ [ОТРЕЗАНО: Вывод превысил 500KB лимит]"

        result_queue.put({"success": True, "output": out_text, "error": None, "files": fs.files, "exec_time": exec_time})

    except SystemExit:
        out_text = buffer.getvalue()
        if len(out_text.encode('utf-8')) > MAX_OUTPUT_BYTES: out_text = "[ОТРЕЗАНО]"
        result_queue.put({"success": True, "output": out_text, "error": None, "files": {}, "exec_time": 0})
    except Exception as e:
        exec_time = round(time.time() - start_time, 4)
        out_text = buffer.getvalue()
        if len(out_text.encode('utf-8')) > MAX_OUTPUT_BYTES: out_text = "[ОТРЕЗАНО]"
        result_queue.put({
            "success": False, "output": out_text,
            "error": f"{type(e).__name__}: {str(e)}\n\n{traceback.format_exc()}",
            "files": {}, "exec_time": exec_time
        })
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

def run_sandbox(code: str, timeout: int) -> dict:
    """Запускает процесс, ждет результат, убивает при зависании"""
    result_queue = Queue()
    process = Process(target=worker_process, args=(code, timeout, result_queue))
    
    process.start()
    process.join(timeout=timeout)
    
    if process.is_alive():
        process.terminate()
        process.join(timeout=1)
        if process.is_alive():
            process.kill() # SIGKILL
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
        if not self.chunks: return ["📭 (пустой вывод)"]
        result_parts, current_part = [], ""
        for chunk in self.chunks:
            if len(current_part) + len(chunk) > MAX_TG_MESSAGE_LEN - 200:
                if current_part: result_parts.append(current_part)
                current_part = chunk
            else: current_part += chunk
        if current_part: result_parts.append(current_part)
        return result_parts if result_parts else ["📭 (пустой вывод)"]

async def send_text_chunks(user_id, context, chunks, error_msg=None):
    """Отправляет части текста с задержкой"""
    for i, chunk in enumerate(chunks):
        prefix = f"📄 Часть {i+1}/{len(chunks)}\n\n" if len(chunks) > 1 else ""
        text = prefix + "<code>" + escape(chunk) + "</code>"
        
        if error_msg and i == len(chunks) - 1:
            text += "\n\n🚨 <b>Ошибка:</b>\n<code>" + escape(error_msg[:1500]) + "</code>"
            
        if len(text) > MAX_TG_MESSAGE_LEN: text = text[:MAX_TG_MESSAGE_LEN - 50] + "\n\n... [Обрезано по лимиту ТГ]"
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode='HTML')
        if i < len(chunks) - 1: await asyncio.sleep(DELAY_BETWEEN_CHUNKS)

# =====================================================================
# === ГЛАВНЫЕ ХЭНДЛЕРЫ ТЕЛЕГРАМ-БОТА =================================
# =====================================================================
async def execute_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    user_code = update.message.text
    
    # Обновление статистики профиля
    user.total_chars_typed += len(user_code)
    if len(user_code) > user.longest_code:
        user.longest_code = len(user_code)
    
    # АНТИ-СПАМ ЗАЩИТА
    current_time = time.time()
    if current_time - user.last_run_time < RATE_LIMIT_SECONDS:
        wait_sec = round(RATE_LIMIT_SECONDS - (current_time - user.last_run_time), 1)
        await update.message.reply_text(f"⏳ Анти-спам. Подождите {wait_sec} сек.")
        return
    user.last_run_time = current_time
    user.run_count += 1

    # В Тихом режиме не показываем "Запуск контейнера..."
    if not user.silent_mode:
        wait_msg = await update.message.reply_text("🐳 Запуск изолированного контейнера...")
    
    result = run_sandbox(user_code, user.timeout)
    
    # Сохраняем тип последней ошибки для статистики
    if not result["success"] and result["error"]:
        match = re.search(r'^(\w+Error)', result["error"])
        if match: user.last_error_type = match.group(1)
    
    if not user.silent_mode:
        try: await wait_msg.delete()
        except: pass

    # ОБРАБОТКА СГЕНЕРИРОВАННЫХ ФАЙЛОВ (Кнопки скачивания)
    reply_markup = None
    if result.get("files"):
        FILES_DB[user_id] = {"files": result["files"], "map": {}}
        buttons = []
        for idx, filename in enumerate(result["files"].keys()):
            btn_id = f"dl_{idx}"
            FILES_DB[user_id]["map"][btn_id] = filename
            display_name = filename if len(filename) <= 30 else "..." + filename[-27:]
            buttons.append([InlineKeyboardButton(f"📂 Скачать {display_name}", callback_data=btn_id)])
        reply_markup = InlineKeyboardMarkup(buttons)

    out_buf = OutputBuffer()
    out_buf.write(result["output"])
    chunks = out_buf.get_chunks_for_telegram()

    # === ЛОГИКА ТИХОГО РЕЖИМА ===
    if user.silent_mode:
        if result["success"]:
            await send_text_chunks(user_id, context, chunks)
        else:
            smart_tip = ErrorAnalyzer.analyze(result["error"])
            error_chunks = []
            if smart_tip: error_chunks.append(smart_tip + "\n")
            error_chunks.extend(chunks)
            await send_text_chunks(user_id, context, error_chunks, result["error"])
        if reply_markup:
            await context.bot.send_message(chat_id=user_id, text="📁 Файлы:", reply_markup=reply_markup)
        return

    # === ЛОГИКА ОБЫЧНОГО РЕЖИМА ===
    safe_code_preview = escape(user_code[:500]) + ("..." if len(user_code) > 500 else "")
    status = "✅ Успешно" if result["success"] else "❌ Ошибка/Таймаут"
    time_info = get_time_emoji(result['exec_time'])
    
    header = (
        f"<b>{status}</b> | {time_info}\n"
        "─────────────────────\n"
        f"💻 <b>Код:</b>\n<code>{safe_code_preview}</code>\n\n"
        "─────────────────────\n"
        "📤 <b>Вывод:</b>\n"
    )

    try:
        await context.bot.send_message(chat_id=user_id, text=header, parse_mode='HTML')
        await asyncio.sleep(0.2)
        
        final_error = None
        smart_tip = None
        if not result["success"] and result["error"]:
            smart_tip = ErrorAnalyzer.analyze(result["error"])
            final_error = result["error"]
            
        if smart_tip:
            await context.bot.send_message(chat_id=user_id, text=smart_tip, parse_mode='HTML')
            await asyncio.sleep(0.1)
            
        await send_text_chunks(user_id, context, chunks, final_error)
        
        if reply_markup:
            await context.bot.send_message(chat_id=user_id, text="📁 <b>Скрипт создал файлы. Нажмите для скачивания:</b>", reply_markup=reply_markup, parse_mode='HTML')
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
        "Отправь мне Python-код, и я выполню его в безопасном контейнере.\n"
        "Теперь с <b>умными ошибками</b> и <b>тихим режимом</b>!"
    )
    await update.message.reply_text(text, reply_markup=get_main_menu(), parse_mode='HTML')

async def menu_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user = get_user(user_id)
    data = query.data

    if data == "menu_settings":
        kb = [
            [InlineKeyboardButton(f"⏱ Таймаут: {user.timeout} сек", callback_data="none")],
            [
                InlineKeyboardButton("➖ 5 сек", callback_data="set_timeout_-5"),
                InlineKeyboardButton("➕ 5 сек", callback_data="set_timeout_+5")
            ],
            [InlineKeyboardButton(f"🔇 Тихий режим: {'ВКЛ' if user.silent_mode else 'ВЫКЛ'}", callback_data="toggle_silent")],
            [InlineKeyboardButton("🔙 Назад", callback_data="menu_back")]
        ]
        text = "⚙️ <b>Настройки контейнера</b>\n\n• <b>Таймаут:</b> Жизнь скрипта (макс. 45с).\n• <b>Тихий режим:</b> Присылает только результат, без лишнего текста."
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

    elif data == "toggle_silent":
        user.silent_mode = not user.silent_mode
        kb = [
            [InlineKeyboardButton(f"⏱ Таймаут: {user.timeout} сек", callback_data="none")],
            [
                InlineKeyboardButton("➖ 5 сек", callback_data="set_timeout_-5"),
                InlineKeyboardButton("➕ 5 сек", callback_data="set_timeout_+5")
            ],
            [InlineKeyboardButton(f"🔇 Тихий режим: {'ВКЛ' if user.silent_mode else 'ВЫКЛ'}", callback_data="toggle_silent")],
            [InlineKeyboardButton("🔙 Назад", callback_data="menu_back")]
        ]
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("set_timeout_"):
        change = int(data.split("_")[2])
        user.timeout = max(MIN_TIMEOUT, min(MAX_TIMEOUT, user.timeout + change))
        kb = [
            [InlineKeyboardButton(f"⏱ Таймаут: {user.timeout} сек", callback_data="none")],
            [
                InlineKeyboardButton("➖ 5 сек", callback_data="set_timeout_-5"),
                InlineKeyboardButton("➕ 5 сек", callback_data="set_timeout_+5")
            ],
            [InlineKeyboardButton(f"🔇 Тихий режим: {'ВКЛ' if user.silent_mode else 'ВЫКЛ'}", callback_data="toggle_silent")],
            [InlineKeyboardButton("🔙 Назад", callback_data="menu_back")]
        ]
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))

    elif data == "menu_profile":
        username = query.from_user.username or "Не задан"
        text = (
            "👤 <b>Твоя детальная статистика</b>\n\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            f"📛 Юзернейм: @{escape(username)}\n"
            f"📅 Первый запуск: {user.first_seen}\n"
            f"⏱ Личный таймаут: {user.timeout} сек\n"
            f"🔇 Тихий режим: {'Включен 🤫' if user.silent_mode else 'Выключен 🔊'}\n\n"
            f"🚀 Запусков выполнено: {user.run_count}\n"
            f"⌨️ Символов напечатано: {user.total_chars_typed}\n"
            f"📏 Самый длинный код: {user.longest_code} символов\n"
            f"💀 Последняя ошибка: <code>{user.last_error_type}</code>\n"
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
            "4. <b>Умные ошибки:</b> Если код не сработает, бот напишет простым языком, в чем проблема (забыл скобку, деление на ноль и т.д.).\n"
            "5. <b>Тихий режим:</b> Включи в настройках, чтобы получать только результат без лишнего текста.\n"
            "6. <b>Таймер:</b> Следи за иконками ⚡/🏃/🐢/🐌, чтобы знать, насколько оптимален твой код."
        )
        kb = [[InlineKeyboardButton("🔙 Назад", callback_data="menu_back")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

    elif data == "menu_back":
        text = "🐳 <b>Mega PySandbox Bot</b>\n\nОтправь мне Python-код!"
        await query.edit_message_text(text, reply_markup=get_main_menu(), parse_mode='HTML')

    elif data.startswith("dl_"):
        if user_id not in FILES_DB:
            await query.answer("Файлы устарели.", show_alert=True); return
        file_map = FILES_DB[user_id].get("map", {})
        files_data = FILES_DB[user_id].get("files", {})
        filename = file_map.get(data)
        if not filename or filename not in files_data:
            await query.answer("Файл не найден.", show_alert=True); return
        file_bytes = files_data[filename]
        await query.answer("📎 Отправляю...", show_alert=False)
        try:
            ext = filename.lower().split('.')[-1] if '.' in filename else ""
            is_image = ext in ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp']
            if is_image:
                await context.bot.send_photo(chat_id=user_id, photo=io.BytesIO(file_bytes), caption=f"🖼 {escape(filename)}")
            else:
                await context.bot.send_document(chat_id=user_id, document=io.BytesIO(file_bytes), filename=filename)
        except Exception as e:
            await context.bot.send_message(chat_id=user_id, text=f"❌ Ошибка файла: {e}")

# =====================================================================
# === ЗАПУСК БОТА ======================================================
# =====================================================================
def main():
    app = Application.builder().token(TOKEN).build()
    
    # Роутинг команд и сообщений
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(menu_callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, execute_code))
    
    print("🐳 Mega PySandbox Bot запущен. Нажми Ctrl+C для остановки.")
    app.run_polling()

if __name__ == "__main__":
    main()
