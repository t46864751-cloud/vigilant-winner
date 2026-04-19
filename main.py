import os
import sys
import io
import builtins as real_builtins
import types
import time
import asyncio
from unittest.mock import MagicMock
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler

TOKEN = "8608832312:AAGKkMZTYMth41mBqiTqjhSYJypFePgtM0s"

# === БЕЗОПАСНЫЙ EVAL ===
class SafeEval:
    def __call__(self, expression, globals=None, locals=None):
        safe_globals = {"__builtins__": {}}
        if globals:
            safe_globals.update({k: v for k, v in globals.items() if not k.startswith('_')})
        try:
            return eval(expression, safe_globals, locals)
        except Exception as e:
            return f"[SAFE EVAL ERROR] {e}"
    
    def __repr__(self):
        return "<built-in function eval>"

# === БЕЗОПАСНЫЙ EXEC ===
class SafeExec:
    def __call__(self, code, globals=None, locals=None):
        if isinstance(code, str) and any(x in code for x in ['__import__', 'import os', 'import sys', 'subprocess', 'open(']):
            return "[SAFE EXEC] Подозрительный код заблокирован"
        safe_globals = {"__builtins__": safe_builtins}
        if globals:
            safe_globals.update(globals)
        try:
            exec(code, safe_globals, locals)
        except Exception as e:
            return f"[SAFE EXEC ERROR] {e}"
    
    def __repr__(self):
        return "<built-in function exec>"

# === ФЕЙКОВАЯ ФАЙЛОВАЯ СИСТЕМА ===
class FakeFileSystem:
    def __init__(self):
        self.files = {}
        self.dirs = {'/tmp', '/home/sandbox', '/app'}
    
    def _get_path(self, path):
        return os.path.normpath(str(path))
    
    def exists(self, path):
        p = self._get_path(path)
        return p in self.files or p in self.dirs or os.path.exists(p)
    
    def open(self, path, mode='r', *args, **kwargs):
        p = self._get_path(path)
        
        if 'w' in mode or 'a' in mode or 'x' in mode:
            if p not in self.files:
                self.files[p] = ""
            return FakeFile(p, self.files, mode)
        
        if p in self.files:
            return FakeFile(p, self.files, mode)
        
        allowed_reads = ['/etc/passwd', '/etc/hostname', '/proc/version', '/proc/cpuinfo']
        if any(p.startswith(a) for a in allowed_reads):
            return open(p, mode, *args, **kwargs)
        
        raise FileNotFoundError(f"[SANDBOX] Нет доступа к: {path}")
    
    def listdir(self, path='.'):
        p = self._get_path(path)
        if p in self.dirs:
            return ['fake1.txt', 'fake2.py', 'sandbox']
        try:
            return os.listdir(p)[:20]
        except:
            return []
    
    def mkdir(self, path, mode=0o777):
        self.dirs.add(self._get_path(path))
    
    def remove(self, path):
        p = self._get_path(path)
        if p in self.files:
            del self.files[p]
    
    def rename(self, src, dst):
        src, dst = self._get_path(src), self._get_path(dst)
        if src in self.files:
            self.files[dst] = self.files.pop(src)
    
    def getcwd(self):
        return "/home/sandbox"
    
    def chdir(self, path):
        pass

class FakeFile:
    def __init__(self, path, fs_storage, mode='r'):
        self.path = path
        self.storage = fs_storage
        self.mode = mode
        self.closed = False
        self.position = 0
        if 'w' in mode or 'a' in mode:
            self.storage[path] = ""
    
    def write(self, data):
        if 'r' in self.mode and 'w' not in self.mode:
            raise IOError("Файл открыт только для чтения")
        self.storage[self.path] += str(data)
        return len(str(data))
    
    def read(self, size=-1):
        content = self.storage.get(self.path, "")
        if size == -1:
            return content[self.position:]
        result = content[self.position:self.position + size]
        self.position += size
        return result
    
    def readline(self):
        content = self.storage.get(self.path, "")
        lines = content[self.position:].split('\n')
        if lines:
            self.position += len(lines[0]) + 1
            return lines[0] + '\n'
        return ''
    
    def readlines(self):
        return self.read().splitlines(keepends=True)
    
    def seek(self, pos):
        self.position = pos
    
    def tell(self):
        return self.position
    
    def close(self):
        self.closed = True
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.close()

# === ФЕЙКОВЫЙ OS ===
class FakeOS:
    def __init__(self, fs):
        self.fs = fs
        self.environ = {
            'HOME': '/home/sandbox',
            'USER': 'sandbox',
            'PATH': '/usr/local/bin:/usr/bin:/bin',
            'PWD': '/home/sandbox',
            'SANDBOX': 'true'
        }
        self.name = 'posix'
        self.sep = '/'
        self.linesep = '\n'
        self.pathsep = ':'
    
    def open(self, path, flags, mode=0o777):
        return self.fs.open(path, 'w')
    
    def close(self, fd):
        pass
    
    def system(self, command):
        dangerous = ['rm', 'mkfs', 'dd', 'fdisk', 'format', 'del', 'rd']
        if any(d in str(command).lower() for d in dangerous):
            return f"[SANDBOX] Команда '{command}' заблокирована"
        return f"[SANDBOX] Выполнено: {command} (фейково)"
    
    def popen(self, command, *args, **kwargs):
        return MagicMock(
            read=lambda: f"[SANDBOX] {command}",
            readline=lambda: "",
            close=lambda: None,
            __enter__=lambda self: self,
            __exit__=lambda *args: None
        )
    
    def listdir(self, path='.'):
        return self.fs.listdir(path)
    
    def mkdir(self, path, mode=0o777):
        return self.fs.mkdir(path, mode)
    
    def makedirs(self, path, exist_ok=False):
        self.fs.mkdir(path)
    
    def remove(self, path):
        return self.fs.remove(path)
    
    def unlink(self, path):
        return self.fs.remove(path)
    
    def rename(self, src, dst):
        return self.fs.rename(src, dst)
    
    def replace(self, src, dst):
        return self.fs.rename(src, dst)
    
    def getcwd(self):
        return self.fs.getcwd()
    
    def chdir(self, path):
        return self.fs.chdir(path)
    
    def getpid(self):
        return 12345
    
    def getppid(self):
        return 1
    
    def urandom(self, n):
        import random
        return bytes(random.randint(0, 255) for _ in range(n))
    
    def __getattr__(self, name):
        if name == 'path':
            return FakePath(self.fs)
        return MagicMock(return_value=f"[SANDBOX os.{name}]")

class FakePath:
    def __init__(self, fs):
        self.fs = fs
        self.sep = '/'
    
    def join(self, *args):
        return os.path.join(*args)
    
    def exists(self, path):
        return self.fs.exists(path)
    
    def isfile(self, path):
        return self.fs._get_path(path) in self.fs.files
    
    def isdir(self, path):
        return self.fs._get_path(path) in self.fs.dirs
    
    def abspath(self, path):
        return "/home/sandbox/" + str(path)
    
    def basename(self, path):
        return str(path).split('/')[-1]
    
    def dirname(self, path):
        return '/'.join(str(path).split('/')[:-1]) or '/'
    
    def getsize(self, path):
        return 1024
    
    def splitext(self, path):
        p = str(path)
        if '.' in p:
            return (p[:p.rfind('.')], p[p.rfind('.'):])
        return (p, '')

# === ФЕЙКОВЫЙ SYS ===
class FakeSys:
    def __init__(self):
        self.stdout = MagicMock(write=lambda x: None, flush=lambda: None)
        self.stderr = MagicMock(write=lambda x: None, flush=lambda: None)
        self.stdin = MagicMock(read=lambda: "", readline=lambda: "fake input\n")
        self.version = "3.11.0 (SANDBOX)"
        self.version_info = (3, 11, 0, 'final', 0)
        self.platform = "linux"
        self.byteorder = "little"
        self.maxsize = 9223372036854775807
        self.path = ['/home/sandbox', '/usr/lib/python3.11']
        self.modules = {}
        self.argv = ['sandbox.py']
        self.executable = '/usr/bin/python3'
        self.prefix = '/usr'
        self.exec_prefix = '/usr'
    
    def exit(self, arg=0):
        raise SystemExit(f"[SANDBOX] exit({arg})")
    
    def __getattr__(self, name):
        return MagicMock(return_value=f"[SANDBOX sys.{name}]")

# === СОЗДАЁМ ПЕСОЧНИЦУ ===
def create_sandbox():
    fs = FakeFileSystem()
    fake_os = FakeOS(fs)
    fake_sys = FakeSys()
    
    safe_builtins = {}
    for name in dir(real_builtins):
        if name.startswith('_'):
            continue
        obj = getattr(real_builtins, name)
        safe_builtins[name] = obj
    
    safe_builtins['eval'] = SafeEval()
    safe_builtins['exec'] = SafeExec()
    safe_builtins['open'] = fs.open
    safe_builtins['input'] = lambda prompt="": "[SANDBOX INPUT]"
    
    import math, random, datetime, time, re, string, itertools, functools, collections
    import statistics, typing, decimal, fractions, hashlib, base64, binascii, inspect
    import textwrap, uuid, html, json, csv, pprint, copy, warnings, traceback, types
    import enum, dataclasses, pathlib, urllib.parse, urllib.request, urllib.error
    import http.client, socket, ssl, calendar, numbers
    import io as io_module, builtins as builtins_module
    
    real_modules = {
        'math': math,
        'random': random,
        'datetime': datetime,
        'time': time,
        're': re,
        'string': string,
        'itertools': itertools,
        'functools': functools,
        'collections': collections,
        'statistics': statistics,
        'typing': typing,
        'decimal': decimal,
        'fractions': fractions,
        'hashlib': hashlib,
        'base64': base64,
        'binascii': binascii,
        'inspect': inspect,
        'textwrap': textwrap,
        'uuid': uuid,
        'html': html,
        'json': json,
        'csv': csv,
        'pprint': pprint,
        'copy': copy,
        'warnings': warnings,
        'traceback': traceback,
        'types': types,
        'enum': enum,
        'dataclasses': dataclasses,
        'pathlib': pathlib,
        'urllib': urllib,
        'urllib.parse': urllib.parse,
        'urllib.request': urllib.request,
        'urllib.error': urllib.error,
        'http': http,
        'http.client': http.client,
        'socket': socket,
        'ssl': ssl,
        'calendar': calendar,
        'numbers': numbers,
        'io': io_module,
        'builtins': builtins_module,
    }
    
    try:
        import requests
        real_modules['requests'] = requests
    except:
        pass
    
    try:
        import numpy
        real_modules['numpy'] = numpy
    except:
        pass
    
    try:
        import pandas
        real_modules['pandas'] = pandas
    except:
        pass
    
    sandbox = {
        '__builtins__': safe_builtins,
        '__name__': '__main__',
        '__doc__': None,
        '__package__': None,
        '__spec__': None,
        '__annotations__': {},
        '__cached__': None,
        '__file__': '/home/sandbox/script.py',
        
        'os': fake_os,
        'sys': fake_sys,
        'subprocess': MagicMock(
            run=lambda *a, **k: MagicMock(returncode=0, stdout='[SANDBOX]', stderr=''),
            Popen=lambda *a, **k: MagicMock(
                communicate=lambda: (b'[SANDBOX]', b''),
                stdout=MagicMock(read=lambda: b''),
                stderr=MagicMock(read=lambda: b''),
                wait=lambda: 0,
                returncode=0
            ),
            call=lambda *a, **k: 0,
            check_output=lambda *a, **k: b'[SANDBOX]',
            check_call=lambda *a, **k: 0
        ),
        
        **real_modules,
    }
    
    return sandbox

# === БУФЕРИЗАЦИЯ ВЫВОДА ===
class OutputBuffer:
    def __init__(self):
        self.chunks = []
        self.current_chunk = ""
    
    def write(self, text):
        self.current_chunk += str(text)
        if '\n' in self.current_chunk:
            lines = self.current_chunk.split('\n')
            for line in lines[:-1]:
                self.chunks.append(line + '\n')
            self.current_chunk = lines[-1]
    
    def flush(self):
        if self.current_chunk:
            self.chunks.append(self.current_chunk)
            self.current_chunk = ""
    
    def get_output(self):
        self.flush()
        return "".join(self.chunks)
    
    def get_chunks_for_telegram(self, max_length=4000):
        self.flush()
        full_output = self.get_output()
        
        if len(full_output) <= max_length:
            return [full_output] if full_output else ["📭 (пустой вывод)"]
        
        chunks = []
        current = ""
        for line in self.chunks:
            if len(current) + len(line) > max_length:
                if current:
                    chunks.append(current)
                current = line
            else:
                current += line
        
        if current:
            chunks.append(current)
        
        if not chunks:
            chunks = [full_output[i:i+max_length] for i in range(0, len(full_output), max_length)]
        
        return chunks if chunks else ["📭 (пустой вывод)"]

def run_sandbox(code: str) -> dict:
    output_buffer = OutputBuffer()
    
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    
    class SandboxStdout:
        def write(self, text):
            output_buffer.write(text)
            old_stdout.write(text)
        def flush(self):
            output_buffer.flush()
            old_stdout.flush()
    
    sys.stdout = SandboxStdout()
    sys.stderr = SandboxStdout()
    
    try:
        sandbox = create_sandbox()
        compiled = compile(code, '<sandbox>', 'exec')
        exec(compiled, sandbox, {})
        
        output_buffer.flush()
        chunks = output_buffer.get_chunks_for_telegram()
        
        return {
            "success": True,
            "chunks": chunks,
            "chunk_count": len(chunks),
            "error": None
        }
        
    except SystemExit as e:
        output_buffer.flush()
        chunks = output_buffer.get_chunks_for_telegram()
        return {
            "success": True,
            "chunks": chunks,
            "chunk_count": len(chunks),
            "error": None
        }
    except Exception as e:
        output_buffer.flush()
        chunks = output_buffer.get_chunks_for_telegram()
        error_msg = f"⚠️ {type(e).__name__}: {str(e)}"
        return {
            "success": False,
            "chunks": chunks,
            "chunk_count": len(chunks),
            "error": error_msg
        }
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

# === ОТПРАВКА СООБЩЕНИЙ С ЗАДЕРЖКОЙ ===
async def send_chunks(update, context, chunks, is_error=False):
    user_id = update.effective_user.id
    
    if is_error and len(chunks) == 1 and not chunks[0].strip():
        chunks = ["📭 (пустой вывод до ошибки)"]
    
    for i, chunk in enumerate(chunks):
        prefix = ""
        if len(chunks) > 1:
            prefix = f"📄 Часть {i+1}/{len(chunks)}\n\n"
        
        text = prefix + chunk
        
        if is_error and i == len(chunks) - 1:
            continue
        
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode=None)
        
        if i < len(chunks) - 1:
            await asyncio.sleep(0.5)

async def send_error(update, context, chunks, error_msg):
    user_id = update.effective_user.id
    
    for i, chunk in enumerate(chunks):
        prefix = ""
        if len(chunks) > 1:
            prefix = f"📄 Часть {i+1}/{len(chunks)}\n\n"
        
        text = prefix + chunk
        
        if i == len(chunks) - 1:
            text = text + "\n\n❌ ОШИБКА:\n" + error_msg
        
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode=None)
        
        if i < len(chunks) - 1:
            await asyncio.sleep(0.5)

async def execute_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_code = update.message.text
    
    result = run_sandbox(user_code)
    
    if result["success"]:
        header = (
            "✅ Успешно выполнено\n\n"
            "🔧 Метод: 🎭 Mega Sandbox\n"
            "💻 Код:\n```python\n" + user_code[:1000] + "\n```\n"
            "📤 Вывод:\n```\n"
        )
        
        if result["chunk_count"] == 1:
            full_text = header + result["chunks"][0] + "\n```"
            if len(full_text) > 4000:
                await context.bot.send_message(chat_id=user_id, text=header, parse_mode='Markdown')
                await asyncio.sleep(0.5)
                await send_chunks(update, context, result["chunks"])
            else:
                await context.bot.send_message(chat_id=user_id, text=full_text, parse_mode='Markdown')
        else:
            await context.bot.send_message(chat_id=user_id, text=header, parse_mode='Markdown')
            await asyncio.sleep(0.5)
            await send_chunks(update, context, result["chunks"])
    else:
        header = (
            "❌ Ошибка выполнения\n\n"
            "🔧 Метод: 🎭 Mega Sandbox\n"
            "💻 Код:\n```python\n" + user_code[:1000] + "\n```\n"
            "📤 Вывод до ошибки:\n```\n"
        )
        
        if result["chunk_count"] == 1:
            full_text = header + result["chunks"][0] + "\n```\n\n🚨 Ошибка: `" + result["error"] + "`"
            if len(full_text) > 4000:
                await context.bot.send_message(chat_id=user_id, text=header, parse_mode='Markdown')
                await asyncio.sleep(0.5)
                await send_error(update, context, result["chunks"], result["error"])
            else:
                await context.bot.send_message(chat_id=user_id, text=full_text, parse_mode='Markdown')
        else:
            await context.bot.send_message(chat_id=user_id, text=header, parse_mode='Markdown')
            await asyncio.sleep(0.5)
            await send_error(update, context, result["chunks"], result["error"])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 Mega PySandbox Bot\n\n"
        "Отправь мне Python-код!\n\n"
        "✅ Настоящие: math, random, datetime, re, json, csv, urllib, http, socket, ssl, hashlib, base64, itertools, collections, statistics, typing, decimal, fractions, inspect, textwrap, uuid, html, pprint, copy, warnings, traceback, types, enum, dataclasses, pathlib, calendar, numbers, io, requests(если установлен), numpy(если установлен), pandas(если установлен)\n\n"
        "🎭 Фейковые: os, sys, subprocess, open(память), eval(безопасный), exec(ограниченный)\n\n"
        "💾 Файлы пишутся в ОЗУ, не на диск!",
        parse_mode=None
    )

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, execute_code))
    app.run_polling()

if __name__ == "__main__":
    main()
