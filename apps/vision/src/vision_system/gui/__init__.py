"""Toolkit-level GUI infrastructure shared by the vision desktop panels.

Nothing here imports ``tkinter`` at module scope: the toolkit is loaded on demand
through :func:`vision_system.gui.toolkit.load_toolkit`, which is what keeps the
package importable — and therefore testable — on a machine with no display.
"""
