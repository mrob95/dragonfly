
from win32com.client import GetObject
from ctypes import windll, c_int, c_ulong, byref
import logging
import sys
import os
import win32security
import subprocess

class InjectionFailure(Exception):
    pass

logger = logging.getLogger(__name__)

def get_pid(proc_name):
    WMI = GetObject('winmgmts:')
    p = WMI.ExecQuery(f'select * from Win32_Process where Name="{proc_name}"')
    if not p:
        raise InjectionFailure(f"Couldn't find process with name '{proc_name}'")
    pid = p[0].Properties_('ProcessId').Value
    return pid

def inject_draconity(injector_path, draconity_path):
    print("Injecting")
    pid = get_pid("natspeak.exe")
    # TODO: Make this work on older python versions
    result = subprocess.run([
        injector_path,
        str(pid),
        draconity_path])
    if result.returncode:
        raise InjectionFailure(result.stdout)
    print("Injected")
