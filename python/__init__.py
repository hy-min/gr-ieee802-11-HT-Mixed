# SPDX-License-Identifier: GPL-3.0-or-later

"""
GNU Radio IEEE802_11 Python package.
"""

# 关键：先导入这些，确保 gr::block / gr::digital::constellation 等 pybind 基类已注册
from gnuradio import gr as _gr  # noqa: F401
from gnuradio import digital as _digital  # noqa: F401

# 再导入本模块的 pybind 扩展
from . import ieee802_11_python as _p  # noqa: F401
from .ieee802_11_python import *       # noqa: F401,F403

# 可选：整理 __all__
__all__ = [n for n in dir(_p) if not n.startswith("_")]
