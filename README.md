![Ultimate Downloader](banner_2x1.png)

# Ultimate Downloader

Download videos and files in Google Colab, save them to Google Drive, and organise shows and movies for Plex. It supports Gofile, Pixeldrain, Mega, YouTube, TorBox, Real-Debrid, PikPak, and many other sources.

**Current version: v7.1.** PikPak support and more reliable live downloads. See the [v7.1 release notes](CHANGELOG.md#v71).

## What's new in v7.1

- **PikPak downloads:** browse shares and account folders, select files, and resume with fresh download links.
- **Live Resume and Retry:** add links and stop downloads during resumed batches and automatic retries.
- **YouTube progress and naming:** follow each video's progress and apply queue edits to videos and subtitles.
- **More reliable transfers:** recover from temporary server errors, keep partial files, and see readable failure details.
- **Compatibility and settings fixes:** restore completion controls, support older OK.ru extractors, and preserve the saved Drive API upload choice.

An [experimental standalone Drive renaming tool](#rename-files-already-in-drive) is also available for existing libraries.

## Get started

You need a Google account and enough space in Drive. A Real-Debrid or TorBox subscription is only needed for premium hosts and magnet links. A free TMDB key is optional for better movie and TV names.

1. Open [Google Colab](https://colab.research.google.com/) and create a new notebook.
2. Paste this into a cell and run it:

```python
import requests; exec(requests.get("https://raw.githubusercontent.com/xersbtt/ultimate-downloader-colab/main/ultimate_downloader.py").text)
```

3. Paste one link per line into **Links**.
4. Click **Resolve Links**, review the queue, then click **Start Download**. For a single link, **Quick Download** starts immediately.

The code runs in Colab; you do not need to install anything on your computer. Colab sessions can end unexpectedly, so use **Resume Previous Session** when you return.

> The command above runs code from this repository. You can [read the script](ultimate_downloader.py) first, or paste its contents directly into a Colab cell. To run a fixed release, replace `main` in the URL with a [published release tag](https://github.com/xersbtt/ultimate-downloader-colab/releases).

### Add your keys

Open **⚙️ Settings** to enter tokens, or save them in Colab's **Secrets** panel (the key icon). Secrets are read again each run and are not saved to Drive.

| Service | Secret name | When you need it |
| --- | --- | --- |
| Gofile | `GOFILE_TOKEN` | Private or account-linked Gofile downloads |
| Real-Debrid | `RD_TOKEN` | Premium hosts and magnets through Real-Debrid |
| TorBox | `TB_TOKEN` | Premium hosts, magnets, and TorBox links |
| PikPak | `PIKPAK_TOKEN` | PikPak shares and files in your own PikPak account |
| TMDB | `TMDB_API_KEY` | Better names, years, anime routing, and season matching |
| FShare | `FSHARE_EMAIL` and `FSHARE_PASSWORD` | FShare VIP links |

Choose **Real-Debrid**, **TorBox**, or **None** with the **Debrid** selector. You can use YouTube, Mega, Gofile, Pixeldrain, and other public sources without a debrid subscription.

### PikPak setup

1. In PikPak, open **Account & Security → Connected Apps → Personal Access Tokens**, then create a token with **Read files** and **Manage files** permissions. See [PikPak's token setup guide](https://mypikpak.com/en-US/help-center/connected_apps/personal_access_tokens/create_personal_access_token).
2. Save the token in Colab Secrets as `PIKPAK_TOKEN`, or enter it in **⚙️ Settings → PikPak Account**. Your token is not saved to Google Drive or passed to the download server.
3. Paste a `https://mypikpak.com/s/...` share link into **Links**, then click **Resolve Links**. Nested folder links browse that folder; root share links include all its folders. Review the queue before starting a large share.
4. Select the files you want and click **Start Download**. Only selected shared files are saved to your PikPak account, and existing Google Drive files are skipped before saving. The downloaded originals use the same organisation and Drive transfer settings as other sources.

Files you already own are reused without creating another account copy. After temporary server errors, PikPak transfers use fewer connections and request fresh links. Failed files show a readable reason, with tokens and signed URLs hidden.

The official PikPak client installs automatically in the Colab runtime on first use. This integration works independently of the **Debrid** selector. It also accepts `pikpak://folder/FOLDER_ID`, `pikpak://file/FILE_ID`, and PikPak `drive/all` URLs with a `parent_id`. For a protected share, append `?pass_code=CODE` to its link (URL-encode the code if it contains special characters).

**Resume Previous Session** and **Retry Failed** reuse saved PikPak file IDs and pending save references, and request fresh download links. If PikPak is still saving a selected file, retry shortly. Saved copies stay in your PikPak account; remove them there when you no longer need them. Account storage and transfer limits apply; check [PikPak's current connected-app limits](https://mypikpak.com/en-US/connect-apps-faq).

## Use the queue

**Resolve Links** shows the files before downloading. You can remove unwanted files, change their order, and see where each one will be saved. Click a row to select it, Shift-click to select a range, or use the checkbox at the top to select or clear all. Drag a column edge to resize the queue table.

After resolving, the Links field is ready for more URLs and the button becomes **Add Links**. This also works during a reviewed download, **Resume Previous Session**, or **Retry Failed**: add links, review the new files, and click **Add to Download**. Downloads already in progress continue. Resumed runs retain completed files, partial downloads, and saved account file IDs. TorBox files in folders such as Extras or Trailers appear in the queue but start unselected; select them if you want them.

Use **⏹ Stop Download** to save unfinished work for Resume, including during resumed runs and their automatic retries. **🔁 Retry Failed** retries failures. For Quick Download, use Colab's **Runtime → Interrupt execution** to stop.

Automatic retries stay in the same live batch and use the configured **Auto Retry** budget. When a live batch finishes, the toolbar restores its controls and shows **Retry Failed** if work remains. The final status stays visible, including when the batch finishes before the first display refresh.

### Follow progress and retries

The per-file progress panel shows percentage and speed, including for YouTube videos added to a running batch. YouTube rows also show preparation, video processing, and transfer to Drive. Colab's live display and the standard progress panel update throughout these stages.

Transfers through aria2 automatically wait 15, 30, and 60 seconds after temporary HTTP 500/502/503/504 server failures, with at most three server cooldowns per file. The progress panel shows the retry countdown, and partial files are kept for continuation. These waits are separate from **Auto Retry**, which retries failed files after a batch. If recovery fails, the saved error and completion log include the HTTP status and actual number of attempts, with tokens and signed URLs hidden.

### Fix a name or destination

The queue previews each file's destination. If a name or episode number looks wrong, select the affected rows and use **Fix Match**, **Force Name / Year**, **Force Season**, **Renumber**, or **Route as**. Fill several fields and click **Apply Changes** to apply them together. Your choices are saved for Resume.

YouTube queue edits apply to duplicate checks and the final video and subtitle destinations, including videos added during a running batch. Resume and Retry retain your saved edits.

With a TMDB key, the downloader can find official names and years, match anime to the anime folders, and place high-numbered anime episodes in the right season. A manual **Route as** choice takes priority.

## Where files go

With **Auto-organise** on, TV episodes go to `My Drive/TV Shows`, movies to `My Drive/Movies`, and recognised anime to `My Drive/Anime Series` or `My Drive/Anime Movies`. YouTube videos without an episode pattern go to `My Drive/YouTube`. Turn Auto-organise off to keep original filenames in `My Drive/Downloads`.

You can change these folders in **⚙️ Settings**. Existing subtitles are kept with their videos. You can also extract text subtitles from videos after download or scan an existing Drive folder with **Extract from Library**.

RAR, ZIP, and 7Z archives are unpacked automatically. Files already downloaded are skipped when possible.

### Rename files already in Drive

**Experimental standalone tool.** The [renaming script](rename_existing_files.py) runs in its own Colab notebook and is not integrated into the downloader. It mounts the same Drive, reads the download folder settings from `My Drive/Ultimate Downloader/settings.json`, and uses `TMDB_API_KEY` from Colab Secrets. Try it on a small test folder and review every preview before moving files. In a new Colab notebook, run:

```python
import requests; exec(requests.get("https://raw.githubusercontent.com/xersbtt/ultimate-downloader-colab/main/rename_existing_files.py").text)
```

Click **Browse Drive**, choose a folder with **Use this folder**, then choose **Downloader style**, **Season to absolute**, or **Absolute to season** and click **Preview files**. **Include subfolders** is on by default. Check the old and new paths, select the files you want, then click **Move selected files**. You can force a name, year, library route, or TMDB match (`tv:12345` or `movie:12345`) for a preview; use one show or movie folder at a time when forcing a match.

**Downloader style** organises TV episodes and movies into your configured libraries, with TMDB names, years, and anime routing when a key is available. Filename-only organisation also works without a key. The script handles episode ranges and Specials, keeps matching subtitle sidecars with their videos, and preserves subtitle language, forced, and SDH tags. Files without an identifiable title and subtitles without a matching video are skipped. Existing destinations and conflicting preview rows are skipped; files changed since the preview must be reviewed again before moving.

**Season to absolute** converts `S02E03` to `S01E15` when TMDB says season 1 has 12 episodes, and moves the result into `Season 01`, matching the downloader's Absolute convention. It needs TMDB counts for every preceding season. If those counts are missing or the episode exceeds the reported season count, the file is skipped for review.

**Absolute to season** reverses that conversion: `S01E15` or a bare `15` becomes `S02E03` in `Season 02` using TMDB season counts. It skips files when the TMDB match or counts are missing, the number exceeds the known episode total, or a multi-episode file crosses a season boundary.

### Download and Drive settings

| Setting | What it does |
| --- | --- |
| **Parallel DLs** | Runs up to five supported downloads at once. Lower it if a provider limits connections. |
| **Auto Retry** | Retries failed files automatically after a batch. Leave it empty to turn it off. |
| **Upload to Drive via API** | Optional Drive transfer method that confirms the file is on Drive before reporting success. It may be faster for large batches. |
| **Overlap Drive moves with downloads** | Starts the next download while finished files move to Drive; uses more Colab disk space. |
| **Drive movers** | Controls how many Drive transfers run together when overlap is on. Most useful with the Drive API option. |

The Drive API choice is restored after mounting Drive. When enabled, reviewed downloads, Resume, and Retry request Google access before starting the batch.

The downloader pauses new downloads when Colab's temporary disk runs low. If large files keep pausing, reduce **Parallel DLs** or **Drive movers**.

## Supported sources

- **Public links:** Gofile, Pixeldrain, direct HTTP, Mega, Archive.org, and more.
- **Video sites:** YouTube and playlists, OK.ru, Twitch, Vimeo, TikTok, Dailymotion, and SoundCloud.
- **Debrid links:** Real-Debrid and TorBox support magnet files and many premium file hosts. TorBox share links and JDownloader folder links are supported.
- **FShare:** File and folder links with a VIP account. This integration is experimental and may need updates when FShare changes its site. Resolving a single file uses your daily quota; browsing a folder does not.
- **PikPak:** Public shares, nested shared folders, and files or folders in your own account, using a Personal Access Token. Premium is recommended for large batches; free-account storage and transfer restrictions still apply.

For YouTube playlists, you can choose a range such as `1,3,5-10` and select subtitle languages in the queue. YouTube cookies for Premium or members-only videos can be uploaded in **⚙️ Settings**; this option is experimental.

## If something goes wrong

| Problem | Try this |
| --- | --- |
| Colab stopped or the download was interrupted | Run the script again and click **Resume Previous Session**. |
| Some files failed | Click **Retry Failed**. For a throttled provider, wait a little before retrying. |
| HTTP 500/502/503/504 appears during a transfer | Let the automatic server countdown finish. If the file still fails, check its saved error and use **Retry Failed** later. |
| PikPak reports a token, permission, storage, or quota error | Check **Read files** and **Manage files**, replace an expired token in Secrets or Settings, and check your account's storage and Connected Apps allowance. |
| OK.ru reports "the JSON object must be str, bytes or bytearray, not dict" | Run v7.1 or later, which includes the metadata compatibility fix. Both `ok.ru` and `odnoklassniki.ru` links are supported. |
| A file has the wrong name or folder | Check its queue destination and use **Force Name**, **Fix Match**, or **Route as**. |
| YouTube skips a video | Clear the YouTube archive in **⚙️ Settings**. If you uploaded cookies, try clearing them too. |
| A FShare login fails | Try resolving again; if it keeps failing, restart the Colab runtime and check your VIP account. |
| Drive transfers are slow or disk space is tight | Try **Upload to Drive via API**, or lower **Parallel DLs** and **Drive movers**. |

For full release history and detailed fixes, see [CHANGELOG.md](CHANGELOG.md).

## Development and releases

See [RELEASING.md](RELEASING.md) for local checks, the Colab smoke test, and the push and release steps. Previous script versions are kept in [archive/](archive/).

## License and credits

[MIT License](LICENSE). Built with [yt-dlp](https://github.com/yt-dlp/yt-dlp), [aria2](https://aria2.github.io/), and [megatools](https://megatools.megous.com/). This product uses the TMDB API but is not endorsed or certified by TMDB.
