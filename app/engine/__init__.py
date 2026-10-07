"""缠论分析引擎。

导入本包时自动把 vendored 的 chan.py 源码目录加入 sys.path,
使得 `from Chan import CChan` 等绝对导入可用。
"""
import os
import sys

_VENDOR_CHAN = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "vendor", "chan.py")
)
if _VENDOR_CHAN not in sys.path:
    sys.path.insert(0, _VENDOR_CHAN)
