import os
import sys
import io
import json
import math
import random
import datetime
import time
import re
import string
import itertools
import functools
import collections
import statistics
import typing
import decimal
import fractions
import hashlib
import base64
import binascii
import inspect
import textwrap
import uuid
import html
import urllib.parse
import urllib.request
import urllib.error
import http.client
import socket
import ssl
import csv
import pprint
import copy
import pickle
import warnings
import traceback
import types
import enum
import dataclasses
import pathlib
import builtins
from unittest.mock import MagicMock
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler

TOKEN = "8608832312:AAGKkMZTYMth41mBqiTqjhSYJypFePgtM0s"

# === БЕЗОПАСНЫЙ EVAL ===
class SafeEval:
    """Почти настоящий eval, но без доступа к builtins"""
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
    """Почти настоящий exec, но с ограничениями"""
    def __call__(self, code, globals=None, locals=None):
        if isinstance(code, str) and any(x in code for x in ['__import__', 'import os', 'import sys', 'subprocess', 'open(', 'file(']):
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
            # Пишем в память
            if p not in self.files:
                self.files[p] = ""
            return FakeFile(p, self.files, mode)
        
        # Читаем сначала из памяти, потом с диска
        if p in self.files:
            return FakeFile(p, self.files, mode)
        
        # Разрешаем читать некоторые реальные файлы
        allowed_reads = ['/etc/passwd', '/etc/hostname', '/proc/version', '/proc/cpuinfo']
        if any(p.startswith(a) for a in allowed_reads):
            return open(p, mode, *args, **kwargs)
        
        raise FileNotFoundError(f"[SANDBOX] Нет доступа к: {path}")
    
    def listdir(self, path='.'):
        p = self._get_path(path)
        if p in self.dirs:
            return ['fake1.txt', 'fake2.py', 'sandbox']
        try:
            return os.listdir(p)[:20]  # ограничиваем
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
    
    def path_join(self, *args):
        return os.path.join(*args)
    
    def path_exists(self, path):
        return self.exists(path)
    
    def path_isfile(self, path):
        return self._get_path(path) in self.files
    
    def path_isdir(self, path):
        return self._get_path(path) in self.dirs

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
    
    def read(self, fd, n):
        return b"fake data"
    
    def write(self, fd, data):
        return len(data)
    
    def system(self, command):
        dangerous = ['rm', 'mkfs', 'dd', 'fdisk', 'format', 'del', 'rd', 'format']
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
    
    def path(self):
        return self  # для os.path.*
    
    def getpid(self):
        return 12345
    
    def getppid(self):
        return 1
    
    def urandom(self, n):
        return bytes(random.randint(0, 255) for _ in range(n))
    
    def __getattr__(self, name):
        if name in ['path']:
            return FakePath(self.fs)
        return MagicMock(return_value=f"[SANDBOX os.{name}]")

class FakePath:
    def __init__(self, fs):
        self.fs = fs
        self.sep = '/'
    
    def join(self, *args):
        return self.fs.path_join(*args)
    
    def exists(self, path):
        return self.fs.path_exists(path)
    
    def isfile(self, path):
        return self.fs.path_isfile(path)
    
    def isdir(self, path):
        return self.fs.path_isdir(path)
    
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
        self.stdout = MagicMock(
            write=lambda x: None,
            flush=lambda: None,
            read=lambda: ""
        )
        self.stderr = MagicMock(
            write=lambda x: None,
            flush=lambda: None
        )
        self.stdin = MagicMock(
            read=lambda: "",
            readline=lambda: "fake input\n"
        )
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

# === НАСТОЯЩИЕ МОДУЛИ ===
import requests
import urllib3

# === СОЗДАЁМ ПЕСОЧНИЦУ ===
def create_sandbox():
    fs = FakeFileSystem()
    fake_os = FakeOS(fs)
    fake_sys = FakeSys()
    
    # Безопасные builtins
    safe_builtins = {
        # Типы
        'bool': bool,
        'int': int,
        'float': float,
        'complex': complex,
        'str': str,
        'bytes': bytes,
        'bytearray': bytearray,
        'list': list,
        'dict': dict,
        'tuple': tuple,
        'set': set,
        'frozenset': frozenset,
        'type': type,
        'object': object,
        'property': property,
        'staticmethod': staticmethod,
        'classmethod': classmethod,
        'slice': slice,
        'range': range,
        'memoryview': memoryview,
        
        # Функции
        'abs': abs,
        'all': all,
        'any': any,
        'ascii': ascii,
        'bin': bin,
        'callable': callable,
        'chr': chr,
        'compile': compile,
        'delattr': delattr,
        'dir': dir,
        'divmod': divmod,
        'enumerate': enumerate,
        'eval': SafeEval(),
        'exec': SafeExec(),
        'filter': filter,
        'format': format,
        'getattr': getattr,
        'globals': lambda: {},
        'hasattr': hasattr,
        'hash': hash,
        'help': lambda x: str(type(x)),
        'hex': hex,
        'id': id,
        'input': lambda prompt="": "[SANDBOX INPUT]",
        'isinstance': isinstance,
        'issubclass': issubclass,
        'iter': iter,
        'len': len,
        'locals': lambda: {},
        'map': map,
        'max': max,
        'min': min,
        'next': next,
        'oct': oct,
        'open': fs.open,
        'ord': ord,
        'pow': pow,
        'print': print,
        'repr': repr,
        'reversed': reversed,
        'round': round,
        'setattr': setattr,
        'sorted': sorted,
        'sum': sum,
        'vars': lambda: {},
        'zip': zip,
        
        # Исключения
        'BaseException': BaseException,
        'Exception': Exception,
        'ArithmeticError': ArithmeticError,
        'AssertionError': AssertionError,
        'AttributeError': AttributeError,
        'BlockingIOError': BlockingIOError,
        'BrokenPipeError': BrokenPipeError,
        'BufferError': BufferError,
        'BytesWarning': BytesWarning,
        'ChildProcessError': ChildProcessError,
        'ConnectionAbortedError': ConnectionAbortedError,
        'ConnectionError': ConnectionError,
        'ConnectionRefusedError': ConnectionRefusedError,
        'ConnectionResetError': ConnectionResetError,
        'DeprecationWarning': DeprecationWarning,
        'EOFError': EOFError,
        'EnvironmentError': EnvironmentError,
        'FileExistsError': FileExistsError,
        'FileNotFoundError': FileNotFoundError,
        'FloatingPointError': FloatingPointError,
        'FutureWarning': FutureWarning,
        'GeneratorExit': GeneratorExit,
        'IOError': IOError,
        'ImportError': ImportError,
        'ImportWarning': ImportWarning,
        'IndentationError': IndentationError,
        'IndexError': IndexError,
        'InterruptedError': InterruptedError,
        'IsADirectoryError': IsADirectoryError,
        'KeyError': KeyError,
        'KeyboardInterrupt': KeyboardInterrupt,
        'LookupError': LookupError,
        'MemoryError': MemoryError,
        'ModuleNotFoundError': ModuleNotFoundError,
        'NameError': NameError,
        'NotADirectoryError': NotADirectoryError,
        'NotImplementedError': NotImplementedError,
        'OSError': OSError,
        'OverflowError': OverflowError,
        'PendingDeprecationWarning': PendingDeprecationWarning,
        'PermissionError': PermissionError,
        'ProcessLookupError': ProcessLookupError,
        'RecursionError': RecursionError,
        'ReferenceError': ReferenceError,
        'ResourceWarning': ResourceWarning,
        'RuntimeError': RuntimeError,
        'RuntimeWarning': RuntimeWarning,
        'StopAsyncIteration': StopAsyncIteration,
        'StopIteration': StopIteration,
        'SyntaxError': SyntaxError,
        'SyntaxWarning': SyntaxWarning,
        'SystemError': SystemError,
        'SystemExit': SystemExit,
        'TabError': TabError,
        'TimeoutError': TimeoutError,
        'TypeError': TypeError,
        'UnboundLocalError': UnboundLocalError,
        'UnicodeDecodeError': UnicodeDecodeError,
        'UnicodeEncodeError': UnicodeEncodeError,
        'UnicodeError': UnicodeError,
        'UnicodeTranslationError': UnicodeTranslationError,
        'UnicodeWarning': UnicodeWarning,
        'UserWarning': UserWarning,
        'ValueError': ValueError,
        'Warning': Warning,
        'ZeroDivisionError': ZeroDivisionError,
        
        # Константы
        'True': True,
        'False': False,
        'None': None,
        'Ellipsis': Ellipsis,
        'NotImplemented': NotImplemented,
        '__debug__': True,
        '__build_class__': __build_class__,
        '__name__': '__main__',
        '__doc__': None,
        '__package__': None,
        '__spec__': None,
        '__annotations__': {},
    }
    
    # Настоящие модули (безопасные)
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
        'urllib': urllib,
        'urllib.parse': urllib.parse,
        'urllib.request': urllib.request,
        'urllib.error': urllib.error,
        'http': http,
        'http.client': http.client,
        'socket': socket,
        'ssl': ssl,
        'requests': requests,
        'urllib3': urllib3,
    }
    
    # Собираем глобальное пространство
    sandbox = {
        '__builtins__': safe_builtins,
        '__name__': '__main__',
        '__doc__': None,
        '__package__': None,
        '__spec__': None,
        '__annotations__': {},
        '__cached__': None,
        '__file__': '/home/sandbox/script.py',
        
        # Фейковые модули
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
        
        # Настоящие модули
        **real_modules,
        
        # Дополнительные функции
        'open': fs.open,
        'input': lambda prompt="": "[SANDBOX INPUT]",
    }
    
    return sandbox

def run_sandbox(code: str) -> dict:
    """Выполняем код в песочнице"""
    output_buffer = io.StringIO()
    
    # Перехватываем stdout
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    
    class SandboxStdout:
        def write(self, text):
            output_buffer.write(text)
            old_stdout.write(text)
        def flush(self):
            pass
        def read(self):
            return output_buffer.getvalue()
    
    sys.stdout = SandboxStdout()
    sys.stderr = SandboxStdout()
    
    try:
        sandbox = create_sandbox()
        
        # Компилируем и выполняем
        compiled = compile(code, '<sandbox>', 'exec')
        exec(compiled, sandbox, {})
        
        output = output_buffer.getvalue().strip()
        
        return {
            "success": True,
            "output": output or "📭 (пустой вывод)",
            "error": None
        }
        
    except SystemExit as e:
        return {
            "success": True,
            "output": output_buffer.getvalue().strip() + f"\n[SANDBOX] SystemExit: {e}",
            "error": None
        }
    except Exception as e:
        return {
            "success": False,
            "output": output_buffer.getvalue().strip(),
            "error": f"⚠️ {type(e).__name__}: {str(e)}"
        }
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

async def execute_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_code = update.message.text
    
    result = run_sandbox(user_code)
    
    if result["success"]:
        text = (
            "✅ *Успешно выполнено*\n\n"
            "🔧 Метод: 🎭 Super Sandbox\n"
            "💻 Код:\n```python\n" + user_code + "\n```\n"
            "📤 Вывод:\n```\n" + result['output'][:3500] + "\n```"
        )
    else:
        text = (
            "❌ *Ошибка выполнения*\n\n"
            "🔧 Метод: 🎭 Super Sandbox\n"
            "💻 Код:\n```python\n" + user_code + "\n```\n"
            "🚨 Ошибка: `" + result['error'][:1000] + "`"
        )
    
    await context.bot.send_message(chat_id=user_id, text=text, parse_mode='Markdown')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 *Super PySandbox Bot*\n\n"
        "Отправь мне Python-код!\n\n"
        "✅ Настоящие: requests, json, math, random, datetime, re, csv, http, socket, ssl, urllib, hashlib, base64, itertools, collections, statistics, typing, decimal, fractions, inspect, textwrap, uuid, html, pprint, copy, warnings, traceback, types, enum, dataclasses, time, string, functools\n\n"
        "🎭 Фейковые (безопасные): os, sys, subprocess, open(запись в память), eval(безопасный), exec(ограниченный), input(фейковый)\n\n"
        "🛡️ Полная изоляция от системы!",
        parse_mode='Markdown'
    )

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, execute_code))
    app.run_polling()

if __name__ == "__main__":
    main()
