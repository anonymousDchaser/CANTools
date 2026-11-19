"""验证打包版 exe 是否真的显示出主窗口（而不是只有启动画面或进程空转）。

判据：在超时时间内出现一个 IsWindowVisible、标题非空、客户区 >= 900x600 的
顶层窗口（主窗口）；启动画面只有 600x380，不满足该尺寸条件。
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

EXE = r"D:/lzxDChaserGithub/IDEATools/CanMsgParser/dist/CanMsgParser/CanMsgParser.exe"
CRASH = os.path.join(os.environ.get("LOCALAPPDATA", ""), "CanMsgParser", "crash.log")

user32 = ctypes.WinDLL("user32", use_last_error=True)
EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def crash_size():
    try:
        return os.path.getsize(CRASH)
    except OSError:
        return -1


def snapshot(pid):
    out = []

    def cb(hwnd, _lparam):
        p = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 2)
        user32.GetWindowTextW(hwnd, buf, n + 2)
        rect = wt.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        out.append((buf.value, rect.right - rect.left, rect.bottom - rect.top))
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return out


before = crash_size()
proc = subprocess.Popen([EXE], cwd=os.path.dirname(EXE))
print("started pid", proc.pid, flush=True)

main_win = None
seen = {}
for i in range(45):
    time.sleep(1.0)
    if proc.poll() is not None:
        print("进程已退出，exit=", proc.poll(), flush=True)
        break
    for title, w, h in snapshot(proc.pid):
        seen[(title, w, h)] = seen.get((title, w, h), 0) + 1
        if w >= 900 and h >= 600 and title:
            main_win = (title, w, h)
            break
    if main_win:
        print("主窗口出现于第 %ds" % (i + 1), flush=True)
        break

print("观测到的所有可见窗口（标题, 宽, 高）-> 出现次数:")
for k, v in seen.items():
    print("   ", k, "x", v, flush=True)
print("main window:", main_win, flush=True)
print("process alive:", proc.poll() is None, flush=True)

after = crash_size()
print("crash.log size %d -> %d" % (before, after), flush=True)
if after > before:
    with open(CRASH, "rb") as f:
        f.seek(before)
        text = f.read().decode("utf-8", "replace")
    print("本次启动新增日志:\n" + text[-2500:], flush=True)

ok = bool(main_win) and proc.poll() is None
print("RESULT:", "OK" if ok else "FAIL", flush=True)

try:
    proc.terminate()
    proc.wait(timeout=10)
except Exception:
    pass
sys.exit(0 if ok else 1)
