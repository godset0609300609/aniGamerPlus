"""Scrapes ``mygather.php`` (the user's "My Anime" tracked list) and
writes it as a ``sn_list.txt``-compatible text file.

Port of legacy ``export_my_anime``. Best-effort: any HTTP failure or
parse error is logged and swallowed — we never crash the whole CLI just
because the list export couldn't be fetched.
"""

from __future__ import annotations

import pathlib
import typing as T

import bs4

if T.TYPE_CHECKING:
    from ..downloader.http_client import AniGamerHttpClient
    from ..logging_ import Logger


_URL = 'https://ani.gamer.com.tw/mygather.php'
_HEADERS = {
    'accept': 'application/json',
    'origin': 'https://ani.gamer.com.tw',
    'authority': 'ani.gamer.com.tw',
    'user-agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/85.0.4183.83 Safari/537.36'
    ),
}


class MyAnimeExporter:
    """Scrapes ``mygather.php`` and writes a sn_list-shaped text file."""

    def __init__(self, client: AniGamerHttpClient, logger: Logger) -> None:
        self._client = client
        self._logger = logger

    def export(self, output_path: pathlib.Path) -> int:
        """Fetch every page of the user's "My Anime" list and write it.

        Returns the number of entries written. On network / parse error
        the output file is still created (possibly empty) and ``0`` is
        returned.
        """
        entries: list[tuple[str, str]] = []
        try:
            entries = self._scrape_all_pages()
        except Exception as exc:  # noqa: BLE001 — best-effort scrape
            self._logger.error(
                None,
                '匯出我的動畫',
                f'scrape failed: {exc}',
                display=False,
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open('w', encoding='utf-8', newline='\n') as fh:
            for sn, name in entries:
                fh.write(f'{sn} all <{name}>\n')
        return len(entries)

    # ------------------------------------------------------------------ internals

    def _scrape_all_pages(self) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        page = 1
        while True:
            response = self._client.get(
                f'{_URL}?page={page}&sort=0',
                extra_headers=_HEADERS,
            )
            if getattr(response, 'status_code', 0) != 200:
                self._logger.error(
                    None,
                    '匯出我的動畫',
                    f'page {page}: HTTP {getattr(response, "status_code", "?")}',
                    display=False,
                )
                break
            soup = bs4.BeautifulSoup(response.text, 'html.parser')
            if soup.text.find('目前沒有訂閱內容') != -1:
                break
            page_entries = self._parse_page(soup)
            if not page_entries:
                break
            entries.extend(page_entries)
            page += 1
            # Hard safety: don't loop more than 100 pages.
            if page > 100:
                break
        return entries

    def _parse_page(self, soup: bs4.BeautifulSoup) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        block = soup.select_one('.theme-list-block')
        if block is None:
            return out
        for anchor in block.select('a'):
            href = str(anchor.get('href') or '')
            name_holder = anchor.select_one('.theme-name')
            if name_holder is None:
                continue
            name = name_holder.text.strip()
            sn = self._resolve_sn(href)
            if sn is None:
                continue
            out.append((str(sn), name))
        return out

    def _resolve_sn(self, href: str) -> str | None:
        """Follow the anime page link to its canonical ``?sn=`` URL."""
        href = str(href).strip()
        if not href:
            return None
        if 'sn=' in href:
            # Already canonical — pull the sn out directly.
            tail = href.split('sn=', 1)[1]
            digits = ''.join(ch for ch in tail if ch.isdigit())
            return digits or None
        # Non-canonical link (e.g. ``animeRef.php?...``) — ask the server
        # to resolve it. We tolerate any lookup failure and skip the row.
        try:
            response = self._client.get(
                f'https://ani.gamer.com.tw/{href}',
                extra_headers=_HEADERS,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort scrape
            self._logger.error(
                None,
                '匯出我的動畫',
                f'sn lookup for {href} failed: {exc}',
                display=False,
            )
            return None
        final_url = getattr(response, 'url', '') or ''
        if 'sn=' in final_url:
            tail = final_url.split('sn=', 1)[1]
            digits = ''.join(ch for ch in tail if ch.isdigit())
            return digits or None
        return None
