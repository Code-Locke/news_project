import os
import sys

# Point Sphinx at the project root so it can import news_at_12, app, and api
sys.path.insert(0, os.path.abspath('..'))

# ── Project information ───────────────────────────────────────────────────────
project   = 'News_at_12'
author    = 'JaeChul Lee (Code-Locke)'
copyright = f'2026, {author}'
release   = '1.0.0'

# ── General configuration ─────────────────────────────────────────────────────
extensions = [
    'sphinx.ext.autodoc',       # Pull docstrings from source files
    'sphinx.ext.viewcode',      # Add [source] links to each function
    'sphinx.ext.napoleon',      # Understand Google-style docstrings
    'sphinx.ext.intersphinx',   # Link to Python standard library docs
]

intersphinx_mapping = {
    'python': ('https://docs.python.org/3', None),
}

# autodoc settings
autodoc_member_order        = 'bysource'  # preserve source file order
autodoc_typehints           = 'description'
add_module_names            = False       # cleaner: show load_config not news_at_12.load_config

templates_path    = ['_templates']
exclude_patterns  = ['_build', 'Thumbs.db', '.DS_Store']

# ── HTML output ───────────────────────────────────────────────────────────────
html_theme = 'furo'

html_theme_options = {
    'sidebar_hide_name': False,
    'light_css_variables': {
        'color-brand-primary':    '#d79921',   # Gruvbox yellow
        'color-brand-content':    '#458588',   # Gruvbox blue
        'color-background-primary': '#fbf1c7', # Gruvbox light bg
    },
    'dark_css_variables': {
        'color-brand-primary':  '#fabd2f',
        'color-brand-content':  '#83a598',
    },
}

html_static_path = ['_static']
html_title       = 'News_at_12'