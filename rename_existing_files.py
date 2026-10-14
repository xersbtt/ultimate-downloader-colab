"""Standalone Colab tool for reviewing and renaming media already on Drive.

Run in one Colab cell with::

    import requests
    exec(requests.get('https://raw.githubusercontent.com/xersbtt/ultimate-downloader-colab/main/rename_existing_files.py').text)

The planner and executor are independent of the notebook UI so they can later be
integrated into Ultimate Downloader without importing its download pipeline.
"""

from __future__ import annotations

import difflib
import html
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import unquote


DRIVE_BASE = Path('/content/drive/My Drive')
SETTINGS_FILE = DRIVE_BASE / 'Ultimate Downloader' / 'settings.json'
TMDB_API_BASE = 'https://api.themoviedb.org/3'
DEFAULT_FOLDERS = {
    'tv': 'TV Shows', 'movie': 'Movies', 'anime_series': 'Anime Series',
    'anime_movie': 'Anime Movies',
}
SETTING_KEYS = {
    'tv': 'tv_path', 'movie': 'movie_path',
    'anime_series': 'anime_series_path', 'anime_movie': 'anime_movies_path',
}
VIDEO_EXTS = {'.mkv', '.mp4', '.avi', '.mov', '.m4v', '.wmv', '.webm', '.flv', '.ts'}
SUB_EXTS = {'.srt', '.ass', '.ssa', '.vtt', '.sub'}
MEDIA_EXTS = VIDEO_EXTS | SUB_EXTS
EPISODE_RE = re.compile(r'(?i)\bS(\d{1,2})EP?(\d{1,4})(?:\s*[-~]\s*E(?:P)?(\d{1,4}))?(?:v\d+)?\b')
NXN_RE = re.compile(r'(?i)\b(\d{1,2})x(\d{1,4})(?:\s*[-~]\s*(?:\d{1,2}x)?(\d{1,4}))?\b')
BARE_EP_RE = re.compile(
    r'(?i)(?:\b(?:episode|episodio|ep|e|tập)[ ._-]*(\d{1,4})\b|'
    r'\[(\d{1,4})\]|(?:^|[ ._-])(\d{1,4})(?=$|[ ._\-\[(]))'
)
YEAR_RE = re.compile(r'\b(?:19|20)\d{2}\b')
TECH_NUMBERS = {360, 480, 540, 720, 1080, 1440, 2160, 4320}


def _inside(root: Path, path: Path) -> bool:
    """Resolve symlinks as well as '..' before reading or moving Drive files."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def drive_folder(root: Path, relative: str) -> Path:
    relative = relative.strip()
    candidate = (root / relative).resolve()
    if not _inside(root, candidate):
        raise ValueError('Folder must be inside My Drive')
    return candidate


def load_folders(root: Path = DRIVE_BASE) -> dict[str, str]:
    folders = dict(DEFAULT_FOLDERS)
    try:
        with (root / 'Ultimate Downloader' / 'settings.json').open(encoding='utf-8') as handle:
            saved = json.load(handle)
        for category, key in SETTING_KEYS.items():
            value = saved.get(key)
            if isinstance(value, str) and value.strip():
                drive_folder(root, value)
                folders[category] = value.strip()
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return folders


def clean_name(value: str) -> str:
    """Use the downloader's Plex-friendly name cleaning conventions."""
    value = unquote(value)
    value = re.sub(r'(?i)\b(?:2160p|1080p|720p|480p|4k|8k|x264|x265|hevc|web-?dl|webrip|bluray|hdr)\b', ' ', value)
    value = re.sub(r'[\[\](){}《》「」【】]', ' ', value)
    value = re.sub(r'[._|｜-]+', ' ', value)
    value = re.sub(r'\s+', ' ', value).strip(' ._-')
    value = re.sub(r'[<>:"/\\|?*]', '_', value).strip(' .')
    return value or 'Unknown Show'


def _valid_number(text: str) -> Optional[int]:
    number = int(text)
    if number in TECH_NUMBERS or 1900 <= number <= 2099 or number == 0:
        return None
    return number


@dataclass(frozen=True)
class ParsedMedia:
    kind: str
    title: str
    year: str = ''
    season: int = 1
    episode: int = 1
    episode_end: Optional[int] = None
    explicit_season: bool = False
    suffix: str = ''


def _subtitle_parts(path: Path) -> tuple[str, str]:
    """Keep language/forced/SDH tags after the renamed video stem."""
    stem = path.stem
    tags = []
    while True:
        match = re.search(r'(?i)\.(?:[a-z]{2,3}(?:-[a-z]{2,4})?|forced|sdh|hi|cc)$', stem)
        if not match:
            break
        tags.insert(0, match.group())
        stem = stem[:match.start()]
    return stem, ''.join(tags)


def parse_media(path: Path, source_root: Path) -> ParsedMedia:
    stem, suffix = _subtitle_parts(path) if path.suffix.lower() in SUB_EXTS else (path.stem, '')
    # A previously organised folder supplies the title when the basename is only
    # an episode marker. This also lets a single show folder be scanned recursively.
    parent_paths = [p for p in path.parents if _inside(source_root, p)]
    source_is_season = bool(re.fullmatch(r'(?i)season\s*\d+|specials', source_root.name))
    if source_is_season:
        parent_paths.append(source_root.parent)
    parent_names = [p.name for p in parent_paths]
    show_parent = next((p for p in parent_paths if not re.fullmatch(
        r'(?i)season\s*\d+|specials', p.name)), None)
    # A bare "03.mkv" in Downloads or a generic batch folder is ambiguous.
    # Trust a parent folder only if it is below the selected source, includes a
    # year, or is clearly a show folder inside an existing series library.
    trusted_parent = bool(show_parent and (
        show_parent != source_root or YEAR_RE.search(show_parent.name) or
        show_parent.parent.name.casefold() in {'tv shows', 'anime series'} or
        source_is_season
    ))
    folder_name = show_parent.name if trusted_parent else ''
    marker = EPISODE_RE.search(stem) or NXN_RE.search(stem)
    explicit = bool(marker)
    season = episode = 1
    end = None
    if marker:
        season, episode = int(marker.group(1)), int(marker.group(2))
        end = int(marker.group(3)) if marker.group(3) else None
    else:
        for candidate in BARE_EP_RE.finditer(stem):
            value = next(group for group in candidate.groups() if group is not None)
            number = _valid_number(value)
            if number is not None:
                marker, episode = candidate, number
                break
        season_folder = next((re.fullmatch(r'(?i)season\s*(\d+)', name)
                              for name in parent_names if re.fullmatch(r'(?i)season\s*(\d+)', name)), None)
        if marker and season_folder:
            season = int(season_folder.group(1))
            explicit = True
    title_text = stem[:marker.start()] if marker else stem
    title_text = re.sub(r'(?i)\b(?:episode|episodio|ep|e|tập)\s*$', '', title_text)
    title_text = title_text.strip(' ._-')
    if not title_text and folder_name and folder_name.casefold() not in {
        'my drive', 'tv shows', 'anime series', 'movies', 'anime movies', 'downloads',
    }:
        title_text = folder_name
    if title_text and clean_name(title_text) == 'Unknown Show' and folder_name:
        title_text = folder_name
    year_match = YEAR_RE.search(title_text)
    year = year_match.group() if year_match else ''
    if year_match:
        title_text = title_text[:year_match.start()]
    # An existing show folder can supply a missing year.
    if not year and folder_name:
        folder_year = YEAR_RE.search(folder_name)
        if folder_year:
            year = folder_year.group()
    title = clean_name(title_text)
    kind = 'tv' if marker else 'movie'
    return ParsedMedia(kind, title, year, season, episode, end, explicit, suffix)


class TMDBClient:
    def __init__(self, api_key: str):
        self.api_key = api_key.strip()
        self._cache: dict[tuple, Optional[dict]] = {}

    def _get(self, endpoint: str, **params) -> dict:
        if not self.api_key:
            raise ValueError('TMDB_API_KEY is required for this lookup')
        import requests
        response = requests.get(
            TMDB_API_BASE + endpoint,
            params=dict(params, api_key=self.api_key), timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def by_id(self, kind: str, tmdb_id: int) -> dict:
        key = ('id', kind, tmdb_id)
        if key not in self._cache:
            raw = self._get(f'/{kind}/{tmdb_id}', append_to_response='keywords')
            self._cache[key] = self._normalize(kind, raw)
        return self._cache[key]

    def search(self, kind: str, title: str, year: str = '') -> Optional[dict]:
        key = ('search', kind, title.casefold(), year)
        if key not in self._cache:
            candidate = None
            for attempt_year in ([year, ''] if year else ['']):
                params = {'query': title, 'include_adult': 'false'}
                if attempt_year:
                    params['first_air_date_year' if kind == 'tv' else 'year'] = attempt_year
                results = self._get(f'/search/{kind}', **params).get('results') or []
                candidate = next((item for item in results[:5] if max(
                    difflib.SequenceMatcher(None, title.casefold(), str(item.get(field) or '').casefold()).ratio()
                    for field in ('name', 'title', 'original_name', 'original_title')
                ) >= 0.60), None)
                if candidate:
                    break
            self._cache[key] = self.by_id(kind, candidate['id']) if candidate else None
        return self._cache[key]

    @staticmethod
    def _normalize(kind: str, raw: dict) -> dict:
        date = raw.get('first_air_date') or raw.get('release_date') or ''
        seasons = {}
        if kind == 'tv':
            for season in raw.get('seasons') or []:
                num, count = season.get('season_number'), season.get('episode_count')
                if isinstance(num, int) and num > 0 and isinstance(count, int) and count > 0:
                    seasons[num] = count
        genres = {entry.get('id') for entry in raw.get('genres') or []}
        country = {entry.get('iso_3166_1') for entry in raw.get('production_countries') or []}
        country.update(raw.get('origin_country') or [])
        keyword_data = raw.get('keywords') or {}
        keywords = keyword_data.get('results') or keyword_data.get('keywords') or []
        keyword_names = {str(item.get('name') or '').casefold() for item in keywords}
        anime = 16 in genres and (raw.get('original_language') == 'ja' or
                                  'JP' in country or bool(keyword_names & {'anime', 'japanese animation'}))
        return {
            'type': kind, 'id': raw['id'],
            'name': clean_name(raw.get('name') or raw.get('title') or ''),
            'year': date[:4], 'seasons': seasons, 'is_anime': anime,
        }


def season_to_absolute(season: int, episode: int, seasons: dict[int, int]) -> int:
    if season < 1 or episode < 1:
        raise ValueError('Specials and episode zero cannot be converted')
    if season > 1 and any(number not in seasons for number in range(1, season)):
        raise ValueError('TMDB lacks the counts for preceding seasons')
    if season in seasons and episode > seasons[season]:
        raise ValueError('Episode exceeds the TMDB season count')
    return sum(seasons[number] for number in range(1, season)) + episode


def absolute_to_season(episode: int, seasons: dict[int, int],
                       strict: bool = False) -> tuple[int, int]:
    if strict:
        if episode < 1 or not seasons or 1 not in seasons:
            raise ValueError('TMDB season counts are needed for absolute conversion')
        for season in range(1, max(seasons) + 1):
            if season not in seasons:
                raise ValueError('TMDB season counts have a gap')
    remaining = episode
    for season in sorted(seasons):
        if season < 1:
            continue
        if remaining <= seasons[season]:
            return season, remaining
        remaining -= seasons[season]
    if strict:
        raise ValueError('Absolute episode exceeds the known TMDB season counts')
    return 1, episode  # Same fallback as the downloader for unknown future episodes.


@dataclass
class RenameOptions:
    source: Path
    folders: dict[str, str]
    recursive: bool = True
    numbering: str = 'Downloader style'  # or 'Season to absolute' / 'Absolute to season'
    route: str = 'Auto'
    forced_name: str = ''
    forced_year: str = ''
    tmdb_ref: str = ''


@dataclass
class RenameItem:
    source: Path
    destination: Optional[Path]
    status: str
    detail: str = ''
    match: str = ''
    size: int = 0
    mtime_ns: int = 0


def _tmdb_for(parsed: ParsedMedia, options: RenameOptions, client: TMDBClient) -> Optional[dict]:
    if options.tmdb_ref:
        match = re.fullmatch(r'(?i)(tv|movie):\s*(\d+)', options.tmdb_ref.strip())
        if not match:
            raise ValueError('TMDB match must be tv:12345 or movie:12345')
        return client.by_id(match.group(1).lower(), int(match.group(2)))
    if not client.api_key or parsed.title == 'Unknown Show':
        return None
    try:
        return client.search(parsed.kind, parsed.title, parsed.year)
    except Exception:
        # A failed automatic lookup must not prevent filename-only organisation.
        return None


def _destination(parsed: ParsedMedia, source: Path, options: RenameOptions,
                 root: Path, match: Optional[dict]) -> Path:
    kind = parsed.kind
    if options.route in ('tv', 'anime_series'):
        kind = 'tv'
    elif options.route in ('movie', 'anime_movie'):
        kind = 'movie'
    elif match:
        kind = match['type']
    category = options.route if options.route != 'Auto' else (
        'anime_series' if kind == 'tv' and match and match['is_anime'] else
        'anime_movie' if kind == 'movie' and match and match['is_anime'] else kind
    )
    folder = drive_folder(root, options.folders[category])
    title = clean_name(options.forced_name or (match['name'] if match else parsed.title))
    if title == 'Unknown Show':
        raise ValueError('Show or movie name could not be identified')
    year = options.forced_year or (match['year'] if match else parsed.year)
    if year and not re.fullmatch(r'(?:19|20)\d{2}', year):
        raise ValueError('Year must be four digits (1900–2099)')
    show_folder = f'{title} ({year})' if year else title
    ext = source.suffix  # Keep the original extension and its case.
    if kind == 'movie':
        if options.numbering != 'Downloader style':
            raise ValueError('Numbering conversion applies to TV episodes only')
        return folder / show_folder / f'{title}{parsed.suffix}{ext}'
    season, episode, end = parsed.season, parsed.episode, parsed.episode_end
    if options.numbering == 'Season to absolute':
        if not parsed.explicit_season:
            raise ValueError('No SxxEyy or NxN season marker to convert')
        if season > 1 and (not match or match['type'] != 'tv'):
            raise ValueError('TMDB TV match is needed for preceding season counts')
        counts = match['seasons'] if match and match['type'] == 'tv' else {}
        episode = season_to_absolute(season, episode, counts)
        end = season_to_absolute(season, end, counts) if end else None
        season = 1  # Downloader's Absolute convention is S01E<absolute>.
    elif options.numbering == 'Absolute to season':
        if parsed.explicit_season and season != 1:
            raise ValueError('Expected an absolute number or S01E<absolute>')
        if not match or match['type'] != 'tv':
            raise ValueError('TMDB TV match is needed to find season boundaries')
        counts = match['seasons']
        season, episode = absolute_to_season(episode, counts, strict=True)
        if end:
            end_season, end = absolute_to_season(end, counts, strict=True)
            if end_season != season:
                raise ValueError('Episode range crosses a TMDB season boundary')
    elif not parsed.explicit_season and match and match['type'] == 'tv':
        season, episode = absolute_to_season(episode, match['seasons'])
    span = f'-E{end:02d}' if end and end > episode else ''
    season_folder = 'Specials' if season == 0 else f'Season {season:02d}'
    filename = f'{title} - S{season:02d}E{episode:02d}{span}{parsed.suffix}{ext}'
    return folder / show_folder / season_folder / filename


def scan_files(source: Path, root: Path, recursive: bool) -> list[Path]:
    if not _inside(root, source) or not source.is_dir():
        raise ValueError('Choose an existing folder inside My Drive')
    if recursive:
        files = []
        for base, dirs, names in os.walk(source, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not (Path(base) / d).is_symlink())
            files.extend(Path(base) / name for name in sorted(names))
    else:
        files = list(source.iterdir())
    return sorted((path for path in files if path.is_file() and not path.is_symlink()
                   and path.suffix.lower() in MEDIA_EXTS and _inside(root, path)),
                  key=lambda path: str(path).casefold())


def plan_renames(options: RenameOptions, client: TMDBClient,
                 root: Path = DRIVE_BASE) -> list[RenameItem]:
    source = options.source.resolve()
    paths = scan_files(source, root, options.recursive)
    # Subtitles are planned after videos so they can inherit the video's exact
    # destination, including a corrected TMDB match and season conversion.
    videos = [path for path in paths if path.suffix.lower() in VIDEO_EXTS]
    subtitles = [path for path in paths if path.suffix.lower() in SUB_EXTS]
    rows: list[RenameItem] = []
    by_video_stem: dict[tuple[Path, str], RenameItem] = {}
    for path in videos + subtitles:
        row = RenameItem(path, None, 'Skipped')
        try:
            stat = path.stat()
            row.size, row.mtime_ns = stat.st_size, stat.st_mtime_ns
            parsed = parse_media(path, source)
            if path in subtitles:
                pair = by_video_stem.get((path.parent, _subtitle_parts(path)[0].casefold()))
                if pair:
                    if pair.destination is None or pair.status not in ('Ready', 'Already correct'):
                        raise ValueError('Matching video has no safe destination')
                    row.destination = pair.destination.with_name(
                        pair.destination.stem + parsed.suffix + path.suffix)
                    row.match = pair.match
                else:
                    raise ValueError('No matching video in the selected folder')
            else:
                match = _tmdb_for(parsed, options, client)
                row.match = (f"{match['type']}:{match['id']} {match['name']}" if match else 'Filename only')
                row.destination = _destination(parsed, path, options, root, match)
                by_video_stem[(path.parent, path.stem.casefold())] = row
            if not _inside(root, row.destination):
                raise ValueError('Destination is outside My Drive')
            if row.destination == path:
                row.status, row.detail = 'Already correct', 'No change needed'
            elif row.destination.exists():
                row.status, row.detail = 'Conflict', 'Destination already exists'
            else:
                row.status, row.detail = 'Ready', ''
        except Exception as exc:
            row.status, row.detail = 'Skipped', str(exc)
            row.destination = None
        rows.append(row)
    destinations: dict[Path, list[RenameItem]] = {}
    for row in rows:
        if row.status == 'Ready':
            destinations.setdefault(row.destination, []).append(row)
    for duplicate_rows in destinations.values():
        if len(duplicate_rows) > 1:
            for row in duplicate_rows:
                row.status, row.detail = 'Conflict', 'Several source files have this destination'
    return sorted(rows, key=lambda row: str(row.source).casefold())


def apply_renames(rows: list[RenameItem], selected: set[Path],
                  root: Path = DRIVE_BASE) -> dict[str, int]:
    """Apply reviewed rows once, rechecking source and destination at move time."""
    results = {'Moved': 0, 'Skipped': 0, 'Failed': 0}
    for row in rows:
        if row.status != 'Ready' or row.source not in selected:
            continue
        try:
            if (row.destination is None or row.source.is_symlink() or
                    not _inside(root, row.source) or not _inside(root, row.destination)):
                raise ValueError('Path is outside My Drive')
            current = row.source.stat()
            if current.st_size != row.size or current.st_mtime_ns != row.mtime_ns:
                raise ValueError('Source changed since preview; review it again')
            if row.destination.exists():
                raise FileExistsError('Destination appeared since preview')
            row.destination.parent.mkdir(parents=True, exist_ok=True)
            if row.destination.exists():
                raise FileExistsError('Destination appeared since preview')
            shutil.move(str(row.source), str(row.destination))
            row.status, row.detail = 'Moved', ''
            results['Moved'] += 1
        except FileExistsError as exc:
            row.status, row.detail = 'Conflict', str(exc)
            results['Skipped'] += 1
        except Exception as exc:
            row.status, row.detail = 'Failed', str(exc)
            results['Failed'] += 1
    return results


def _colab_secret(name: str) -> str:
    try:
        from google.colab import userdata
        return userdata.get(name) or ''
    except Exception:
        return ''


def launch() -> None:
    """Mount the same Drive and show a folder -> preview -> move workflow."""
    import ipywidgets as widgets
    from IPython.display import display
    try:
        from google.colab import drive
    except ImportError:
        drive = None
    if drive is not None and not DRIVE_BASE.exists():
        drive.mount('/content/drive')
    if not DRIVE_BASE.is_dir():
        raise RuntimeError('Google Drive is not mounted at /content/drive/My Drive')

    folders = load_folders()
    client = TMDBClient(_colab_secret('TMDB_API_KEY'))
    source_input = widgets.Text(value=folders['tv'], description='Source:',
                                placeholder='Folder under My Drive',
                                layout=widgets.Layout(width='62%'))
    browse_button = widgets.Button(description='Browse Drive', icon='folder-open')
    refresh_key = widgets.Button(description='Reload TMDB key', icon='refresh')
    browser_label = widgets.HTML()
    browser_folders = widgets.Select(options=[], layout=widgets.Layout(width='400px', height='170px'))
    up_button = widgets.Button(description='Up')
    open_button = widgets.Button(description='Open')
    choose_button = widgets.Button(description='Use this folder', button_style='success')
    close_button = widgets.Button(description='Close')
    browser = widgets.VBox([browser_label, browser_folders,
                            widgets.HBox([up_button, open_button, choose_button, close_button])],
                           layout=widgets.Layout(display='none'))
    browser_path = ['']
    recursive = widgets.Checkbox(value=True, description='Include subfolders', indent=False)
    numbering = widgets.Dropdown(options=['Downloader style', 'Season to absolute',
                                          'Absolute to season'],
                                 description='Numbering:')
    route = widgets.Dropdown(options=[('Auto', 'Auto'), ('TV Shows', 'tv'),
                                      ('Anime Series', 'anime_series'), ('Movies', 'movie'),
                                      ('Anime Movies', 'anime_movie')], description='Route:')
    name_input = widgets.Text(description='Force name:', placeholder='Optional')
    year_input = widgets.Text(description='Year:', placeholder='Optional', layout=widgets.Layout(width='230px'))
    tmdb_input = widgets.Text(description='TMDB match:', placeholder='tv:12345 or movie:12345',
                              layout=widgets.Layout(width='330px'))
    preview_button = widgets.Button(description='Preview files', button_style='primary', icon='search')
    move_button = widgets.Button(description='Move selected files', button_style='success',
                                 icon='check', disabled=True)
    selection = widgets.SelectMultiple(options=[], description='Selected:',
                                       layout=widgets.Layout(width='98%', height='170px'))
    preview = widgets.HTML()
    status = widgets.HTML()
    rows: list[RenameItem] = []

    def esc(value) -> str:
        return html.escape(str(value), quote=True)

    def update_browser() -> None:
        current = drive_folder(DRIVE_BASE, browser_path[0])
        browser_label.value = f'<b>My Drive / {esc(browser_path[0])}</b>'
        options = sorted((entry.name for entry in current.iterdir()
                          if entry.is_dir() and not entry.is_symlink()), key=str.casefold)
        browser_folders.options = options

    def on_browse(_button) -> None:
        try:
            chosen = drive_folder(DRIVE_BASE, source_input.value)
            browser_path[0] = ('' if chosen == DRIVE_BASE else
                               str(chosen.relative_to(DRIVE_BASE))) if chosen.is_dir() else ''
        except (ValueError, OSError):
            browser_path[0] = ''
        update_browser()
        browser.layout.display = 'block'

    def on_up(_button) -> None:
        browser_path[0] = str(Path(browser_path[0]).parent) if browser_path[0] else ''
        if browser_path[0] == '.':
            browser_path[0] = ''
        update_browser()

    def on_open(_button) -> None:
        if browser_folders.value:
            browser_path[0] = str(Path(browser_path[0]) / browser_folders.value)
            update_browser()

    def on_choose(_button) -> None:
        source_input.value = browser_path[0]
        browser.layout.display = 'none'

    def render_rows() -> None:
        options = [(f'{row.status}: {row.source.relative_to(DRIVE_BASE)}', str(row.source))
                   for row in rows if row.status == 'Ready']
        selection.options = options
        selection.value = tuple(value for _, value in options)
        move_button.disabled = not options
        table = ['<div style="max-height:420px;overflow:auto"><table style="border-collapse:collapse;width:100%">',
                 '<tr><th>Status</th><th>From</th><th>To</th><th>Match / reason</th></tr>']
        for row in rows:
            dest = row.destination.relative_to(DRIVE_BASE) if row.destination else '—'
            table.append('<tr>' + ''.join(f'<td style="border:1px solid #ddd;padding:4px">{esc(cell)}</td>'
                                         for cell in (row.status, row.source.relative_to(DRIVE_BASE),
                                                      dest, row.detail or row.match)) + '</tr>')
        preview.value = ''.join(table) + '</table></div>'

    def on_preview(_button) -> None:
        nonlocal rows
        move_button.disabled = True
        try:
            client.api_key = _colab_secret('TMDB_API_KEY') or client.api_key
            options = RenameOptions(
                source=drive_folder(DRIVE_BASE, source_input.value), folders=folders,
                recursive=recursive.value, numbering=numbering.value,
                route=route.value, forced_name=name_input.value.strip(),
                forced_year=year_input.value.strip(), tmdb_ref=tmdb_input.value.strip(),
            )
            if options.tmdb_ref and not client.api_key:
                raise ValueError('Set TMDB_API_KEY in Colab Secrets and reload it')
            rows = plan_renames(options, client)
            render_rows()
            counts = {state: sum(row.status == state for row in rows)
                      for state in ('Ready', 'Already correct', 'Conflict', 'Skipped')}
            status.value = f'<b>Preview:</b> {esc(counts)}. Review destinations, then select files to move.'
        except Exception as exc:
            rows = []
            preview.value = ''
            selection.options = []
            status.value = f'<span style="color:#b00">{esc(exc)}</span>'

    def on_move(_button) -> None:
        move_button.disabled = True
        selected = {Path(value) for value in selection.value}
        results = apply_renames(rows, selected)
        render_rows()
        status.value = f'<b>Finished:</b> {esc(results)}. Preview again to refresh the folder.'

    def on_refresh(_button) -> None:
        client.api_key = _colab_secret('TMDB_API_KEY')
        client._cache.clear()
        status.value = ('TMDB key loaded.' if client.api_key else
                        'TMDB key unavailable. Add TMDB_API_KEY in Colab Secrets and allow notebook access.')

    def invalidate_preview(_change) -> None:
        nonlocal rows
        if not rows:
            return
        rows = []
        move_button.disabled = True
        selection.options = []
        preview.value = ''
        status.value = 'Options changed. Preview again before moving files.'

    for control in (source_input, recursive, numbering, route, name_input, year_input, tmdb_input):
        control.observe(invalidate_preview, names='value')

    browse_button.on_click(on_browse)
    refresh_key.on_click(on_refresh)
    up_button.on_click(on_up)
    open_button.on_click(on_open)
    choose_button.on_click(on_choose)
    close_button.on_click(lambda _button: setattr(browser.layout, 'display', 'none'))
    preview_button.on_click(on_preview)
    move_button.on_click(on_move)
    display(widgets.VBox([
        widgets.HTML('<h3>Rename existing Drive files</h3><p>Choose a folder, preview names and destinations, then move selected files.</p>'),
        widgets.HBox([source_input, browse_button, refresh_key]), browser,
        widgets.HBox([recursive, numbering, route]),
        widgets.HBox([name_input, year_input, tmdb_input]),
        widgets.HTML('<small>Force name, year, route, and TMDB match apply to this preview. Scan one show or movie at a time when using them. Season to absolute writes S01E&lt;absolute&gt; into Season 01; Absolute to season maps S01E&lt;absolute&gt; or a bare number into the matching season folder.</small>'),
        preview_button, status, preview, selection, move_button,
    ]))


if __name__ == '__main__':
    launch()
