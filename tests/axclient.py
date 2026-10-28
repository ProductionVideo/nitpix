"""An accessibility client for tests, aimed at one process only (reads its UI tree;
sends no input): every few ms, AXUIElementCopyHierarchy on the app (the call in the crash report),
plus a walk counting children. Usage: axclient.py <pid> [interval_ms] [seconds]"""
import ctypes, os, sys, time
ax = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
V = ctypes.c_void_p
cf.CFStringCreateWithCString.restype = V; cf.CFStringCreateWithCString.argtypes = [V, ctypes.c_char_p, ctypes.c_uint32]
cf.CFArrayCreate.restype = V; cf.CFArrayCreate.argtypes = [V, ctypes.POINTER(V), ctypes.c_long, V]
cf.CFArrayGetCount.restype = ctypes.c_long; cf.CFArrayGetCount.argtypes = [V]
cf.CFArrayGetValueAtIndex.restype = V; cf.CFArrayGetValueAtIndex.argtypes = [V, ctypes.c_long]
cf.CFRelease.argtypes = [V]
cf.CFGetTypeID.restype = ctypes.c_ulong; cf.CFGetTypeID.argtypes = [V]
cf.CFArrayGetTypeID.restype = ctypes.c_ulong
ax.AXUIElementCreateApplication.restype = V; ax.AXUIElementCreateApplication.argtypes = [ctypes.c_int]
ax.AXUIElementCopyAttributeValue.restype = ctypes.c_int; ax.AXUIElementCopyAttributeValue.argtypes = [V, V, ctypes.POINTER(V)]
ax.AXUIElementGetAttributeValueCount.restype = ctypes.c_int; ax.AXUIElementGetAttributeValueCount.argtypes = [V, V, ctypes.POINTER(ctypes.c_long)]
ax.AXUIElementCopyHierarchy.restype = ctypes.c_int; ax.AXUIElementCopyHierarchy.argtypes = [V, V, V, ctypes.POINTER(V)]
ax.AXUIElementSetMessagingTimeout.argtypes = [V, ctypes.c_float]
s = lambda t: cf.CFStringCreateWithCString(None, t.encode(), 0x08000100)
CH, ROLE, TITLE, VALUE, WINS = s("AXChildren"), s("AXRole"), s("AXTitle"), s("AXValue"), s("AXWindows")
SEL, SROWS, ROWS, VIS = s("AXSelectedChildren"), s("AXSelectedRows"), s("AXRows"), s("AXVisibleChildren")
attrs = (V * 8)(CH, ROLE, TITLE, VALUE, SEL, SROWS, ROWS, VIS)
arr = cf.CFArrayCreate(None, attrs, 8, None)
pid, every = int(sys.argv[1]), (float(sys.argv[2]) if len(sys.argv) > 2 else 30) / 1000
until = time.monotonic() + (float(sys.argv[3]) if len(sys.argv) > 3 else 1e9)
app = ax.AXUIElementCreateApplication(pid)
ax.AXUIElementSetMessagingTimeout(app, 2.0)
n = {"hier": 0, "hier_err": {}, "walk": 0, "elems": 0}

def walk(el, d=0):
    if d > 40 or n["elems"] > 10 ** 9:
        return
    c = ctypes.c_long(0)
    ax.AXUIElementGetAttributeValueCount(el, CH, ctypes.byref(c))
    for a in (SEL, SROWS, ROWS):
        ax.AXUIElementGetAttributeValueCount(el, a, ctypes.byref(c))
    out = V()
    if ax.AXUIElementCopyAttributeValue(el, CH, ctypes.byref(out)) == 0 and out.value:
        k = cf.CFArrayGetCount(out)
        n["elems"] += k
        for i in range(k):
            walk(cf.CFArrayGetValueAtIndex(out, i), d + 1)
        cf.CFRelease(out)

def alive():
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

while alive() and time.monotonic() < until:
    h = V()
    e = ax.AXUIElementCopyHierarchy(app, arr, None, ctypes.byref(h))
    n["hier"] += 1
    if e:
        n["hier_err"][e] = n["hier_err"].get(e, 0) + 1
    elif h.value:
        cf.CFRelease(h)
    walk(app)
    n["walk"] += 1
    time.sleep(every)
print(f"ext_ax: {n['hier']} hierarchy copies (errors {n['hier_err']}), {n['walk']} walks, {n['elems']} children", flush=True)
