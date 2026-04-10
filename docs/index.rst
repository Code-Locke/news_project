News_at_12
==========

A self-hosted RSS headline aggregator built with Python, Flask, and SQLite.
Fetches feeds concurrently, deduplicates articles, and serves them through
a Jinja2 frontend and a versioned REST API with Swagger documentation.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   news_at_12
   app
   api

Overview
--------

News_at_12 is a single-user personal tool designed to aggregate RSS headlines
from multiple sources into one place. It runs on a self-hosted Linux machine
and is accessed through a local web browser.

**Key components:**

- ``news_at_12.py`` — core aggregation engine: fetching, parsing, deduplication, and database storage
- ``app.py`` — Flask application serving the Jinja2 frontend and registering the API blueprint
- ``api.py`` — REST API blueprint with versioned endpoints and Flasgger/Swagger documentation

**Stack:**

- Python 3.12
- Flask + Flasgger
- SQLite (via ``sqlite3``)
- feedparser
- Tailwind CSS (CDN) + Gruvbox light theme

API Reference
-------------

The interactive Swagger UI is available at ``/apidocs`` when the application
is running. The REST API is versioned under ``/api/v1/``.

Indices and tables
------------------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`