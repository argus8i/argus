import requests, ctypes

# 1. Activate tab in Chrome CDP
try:
    r = requests.get("http://localhost:9333/json").json()
    tab = [t for t in r if "kite.zerodha.com" in t.get("url", "")][0]
    requests.get(f"http://localhost:9333/json/activate/{tab['id']}")
    print("Activated Kite tab in Chrome CDP:", tab['id'])
except Exception as e:
    print("CDP error:", e)

# 2. Bring window to front
user32 = ctypes.windll.user32
def callback(hwnd, extra):
    if user32.IsWindowVisible(hwnd):
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            title = buff.value
            if "Kite" in title:
                user32.ShowWindow(hwnd, 9) # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
                print("Brought window to front:", title)
    return True

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
user32.EnumWindows(WNDENUMPROC(callback), 0)
