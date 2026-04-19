"""Static assets shipped with the downloader package.

Currently contains ``danmu_template.ass`` (the ASS subtitle template used by
:class:`app.downloader.danmu.DanmuRenderer`). Loaded via
``importlib.resources`` so it travels with the installed wheel regardless
of the caller's working directory.
"""
