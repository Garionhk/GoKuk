"""What the Gokuk builds leave out, and why (after EasyAI's build_common.py).

The build machine's Python carries torch, transformers, numpy and the rest.
Gokuk and Gokuk Setup never import any of them - the engines run in their own
downloaded runtimes - so anything that is merely importable must be kept out,
or a 40 MB download becomes 4 GB.

If a program ever does start using one of these, delete the line: a needed
module left in this list is an ImportError in a windowed build.
"""
from __future__ import annotations

UNUSED_PACKAGES = [
    # The engines. They live in runtime/, never inside the exe.
    "torch", "torchaudio", "torchvision", "transformers", "tokenizers", "safetensors",
    "accelerate", "huggingface_hub", "yue2", "triton", "vllm", "sympy", "networkx",
    "numpy", "scipy", "pandas", "soundfile", "librosa", "numba", "llvmlite",
    "cv2", "PIL", "matplotlib", "tkinter", "IPython", "jupyter", "notebook",
    "cryptography", "yaml", "pytest",
]

UNUSED_QT = [
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.Qt3DCore", "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets", "PySide6.QtOpenGLWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtPositioning",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner",
]

EXCLUDES = UNUSED_PACKAGES + UNUSED_QT

#: Qt libraries PySide6's hook copies wholesale whether or not they are imported.
UNUSED_QT_LIBRARIES = ("qt6quick", "qt6qml", "qt6pdf", "qt6webengine", "qt63d", "qt6charts",
                       "qt6datavisualization", "qt6designer")

#: Kept on purpose: opengl32sw.dll is Qt's fallback when a machine has no usable
#: OpenGL driver. A build that cannot draw its window is worse than 7 MB more.
KEPT_ON_PURPOSE = ("opengl32sw.dll",)


def strip_unused(binaries):
    kept = []
    for entry in binaries:
        name = str(entry[0]).replace("\\", "/").rsplit("/", 1)[-1].lower()
        if any(name.startswith(unused) for unused in UNUSED_QT_LIBRARIES):
            continue
        kept.append(entry)
    print(f"[build_common] left out {len(binaries) - len(kept)} unused Qt libraries")
    return kept
