"""
theme.py — shared theme helper for validate_devocional_gui

Provides a reusable `apply_theme(root, style, output, dark: bool)` function
that applies the same light/dark theme logic taken from
`validate_devocional_gui.py` so other GUIs can reuse it.
"""

from typing import Any


def apply_theme(root: Any, style: Any, output: Any, dark: bool = False) -> None:
    """Apply light or dark color scheme to widgets.

    Parameters:
    - root: Tk root/window (has .configure)
    - style: ttk.Style instance
    - output: non-ttk text widget (e.g., ScrolledText) to configure colors
    - dark: whether to enable dark mode
    """
    # Dracula theme colors
    if dark:
        bg = "#282a36"
        frame_bg = "#282a36"
        fg = "#f8f8f2"
        entry_bg = "#44475a"
        text_bg = "#282a36"
        select_bg = "#6272a4"
    else:
        bg = None
        frame_bg = None
        fg = None
        entry_bg = None
        text_bg = None
        select_bg = None

    # Root/background
    try:
        root.configure(background=bg)
    except Exception:
        try:
            root.configure(background=None)
        except Exception:
            pass

    # Ttk styles
    try:
        if dark:
            s = style
            s.theme_use(s.theme_use())
            s.configure(".", background=frame_bg, foreground=fg)
            s.configure("TLabelFrame", background=frame_bg)
            s.configure("TLabel", background=frame_bg, foreground=fg)
            s.configure("TButton", background=frame_bg, foreground=fg)
            s.configure(
                "TEntry", fieldbackground=entry_bg, background=entry_bg, foreground=fg
            )
            s.configure(
                "Treeview", background=entry_bg, fieldbackground=entry_bg, foreground=fg
            )
            s.map("Treeview", background=[("selected", select_bg)])
            s.configure("Treeview.Heading", background=frame_bg, foreground=fg)
        else:
            try:
                style.theme_use(style.theme_use())
            except Exception:
                pass
    except Exception:
        pass

    # Non-ttk widgets (Text)
    try:
        if dark:
            output.config(background=text_bg, foreground=fg, insertbackground=fg)
        else:
            output.config(
                background="white", foreground="black", insertbackground="black"
            )
    except Exception:
        pass
