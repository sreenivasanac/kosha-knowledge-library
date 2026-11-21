# Kosha Capture (Chrome extension)

Sends your X bookmarks and LinkedIn saved posts to your Kosha server, and can remove at the source the items you archived in Kosha. It works inside your own signed-in browser session: it records what the sites already send to your browser and presses the same buttons you would. It never sees your passwords.

The envelope it posts is described in [`../docs/CAPTURE_CONTRACT.md`](../docs/CAPTURE_CONTRACT.md). Kosha parses the raw data on the server.

## Build

```sh
cd extension
pnpm install
pnpm build   # type-checks, then writes dist/
pnpm test    # vitest
```

## Load

1. Open `chrome://extensions` and turn on **Developer mode**.
2. Press **Load unpacked** and choose `extension/dist`.
3. After each `pnpm build`, press the reload arrow on the extension's card, then reload any open X or LinkedIn tab.

Chrome 111 or later is needed.

## Configure

Open the extension's popup.

| Field | Meaning |
|---|---|
| Kosha server URL | Default `http://localhost:8000`. For a server that is not on this computer, Chrome asks for access to that address when you press Save. |
| API token | Sent as `Authorization: Bearer <token>`. Kept in `chrome.storage.local`, which is not encrypted. |
| Test connection | Calls `GET /api/health` with the token and says whether the server was reached and accepted it. |

The popup also shows the last capture (when, what, and the server's answer) and how many captures are still waiting to be sent.

Defaults, pacing and names are constants in `src/config.ts`. The LinkedIn page selectors are in `src/linkedin/selectors.ts`.

## What each button does

### X bookmarks

**Passive capture (no button).** Whenever you open your bookmarks on X (`x.com/i/bookmarks`, which X now shows at `x.com/i/history`) or a bookmark folder, every page of bookmarks X loads is copied to Kosha. A small panel at the bottom right shows the count.

**Backfill** (popup, or the panel on the bookmarks page). Loads the bookmarks page and scrolls it to the end for you, then opens each bookmark folder and scrolls that too.

- 2.5 to 6 seconds between scrolls, an occasional small scroll up, and a 20 to 60 second rest every 20 to 35 pages.
- If X answers "too many requests" (HTTP 429), it waits 6 to 12 minutes.
- A list is finished after two responses with no posts, or 30 scrolls that bring nothing new.
- **Pause**, **Resume** and **Stop** are in the popup and in the panel. Progress is saved. Resume from the popup also works after the tab or the browser was closed: folders already done are skipped, but the main list starts again from the top, because X gives no way to jump into the middle of it. Kosha ignores what it already has.
- Keep the tab visible while it runs. Chrome slows down hidden tabs and X may stop loading.

### LinkedIn saved posts

**Open saved posts** (popup) opens `linkedin.com/my-items/saved-posts/`. On that page, **Capture** (panel) reads the posts shown, then scrolls 4 to 9 seconds apart for more. It expands "see more" only when that is a plain button; it never follows a link. It stops when five scrolls in a row show nothing new, when you press Stop, or at once if it sees a sign-in page, a captcha or any dialog.

### Remove from sources

Removes, at X and LinkedIn, the items Kosha lists as pending removal (the ones you archived). The first click only asks Kosha how many there are; a second button, showing the numbers, starts the run. **Stop** ends it after the current item. The Kosha web app can start the same run (see "For the web app" below). Nothing else can: pages on X or LinkedIn cannot trigger it.

- **X:** opens each post in a background tab, checks that the bookmark button shows "bookmarked", presses it, checks that it changed, closes the tab. 3 to 8 seconds between posts.
- **LinkedIn:** on the saved-posts page (opened in front if it is not open), finds the post, opens its "..." menu, chooses Unsave, and accepts the confirmation if LinkedIn asks. 8 to 20 seconds between posts, at most 40 per run; the rest wait for the next run.
- The run ends at once on a sign-in page, a captcha, or any dialog other than the Unsave confirmation.
- Each result (`removed`, `already_gone`, `failed`) is reported to Kosha as it happens.

## Risks

- **Removal cannot be undone from here.** A removed bookmark or saved post is gone at the source. Kosha keeps its copy until the extension confirms, but if Kosha's copy is incomplete you cannot fetch the post again from your bookmarks.
- **LinkedIn restricts accounts for automated activity.** Capture and removal are paced slowly and stop at the first sign of trouble, but LinkedIn does not publish its limits, so the risk is low rather than zero. Removal is the riskier of the two because it changes your account.
- **X may rate-limit or challenge the account** during a long backfill. The extension only scrolls and waits when X says to, but a backfill of thousands of bookmarks is unusual activity.
- **Both sites change their pages.** When they do, capture may silently find nothing, or removal may fail. Removal is written to fail rather than guess: if it recognises no saved post at all it stops instead of reporting posts as already gone.
- **`already_gone` is a judgement.** On LinkedIn it means the list was scrolled to its end without finding the post. If LinkedIn stops loading more posts early, a post that is still saved could be reported this way.
- **The API token is stored unencrypted** in the browser profile.
- **Everything X sends for your bookmarks goes to the server URL you set.** Point it only at your own Kosha.

## What is untested

Unit tests cover URL matching, envelope building, the outbox (retry, backoff, restart), backfill pacing and stop rules, the removal queue, and the page logic against hand-built HTML. Matching and post counting were also checked against the real captures in `data/captures/x`.

Not yet run in a browser at all:

- **The built extension as a whole**: loading it, the popup, the path page script → content script → service worker → server. The capture logic in the page is the same as the script that already worked in the browser, but this packaging of it has not been loaded.
- **Backfill scrolling** on the live bookmarks page, including whether scrolling to the end reliably makes X load the next page, and what the page does after a 429.
- **Bookmark folders**: the folder address (`x.com/i/bookmarks/<id>`), the `BookmarkFolderTimeline` response (no sample exists yet) and the name of the folder id in its request (`bookmark_collection_id`) are assumptions. If X does not open a folder address, that folder is skipped and the panel says so.
- **Every LinkedIn selector.** `src/linkedin/selectors.ts` was written from memory of LinkedIn's markup, and `tests/fixtures/linkedin-saved-posts.html` is a hand-written imitation, not a saved page. Expect to correct the selectors on first use. If Capture reports "no posts recognised" or "posts could not be read", that is the cause.
- **Removal on both sites.** It has never been run against X or LinkedIn. The X button names (`data-testid="bookmark"` / `"removeBookmark"`) and whether X renders a post in a background tab are assumptions. Try it first with one or two items.
- **The server**: `GET /api/health`, `GET /api/removals/pending` and `POST /api/removals/result` did not exist when this was written.

There are no screenshots here yet, for the same reason.

## For the web app

`manifest.json` lets pages on `http://localhost/*` and `http://127.0.0.1/*` (any port) message the extension; the service worker then accepts only the origin of the configured server URL. For another host, add it to `EXTERNALLY_CONNECTABLE` in `src/config.ts` and rebuild.

```js
chrome.runtime.sendMessage(EXTENSION_ID, { type: 'kosha:ping' });                // { ok, version }
chrome.runtime.sendMessage(EXTENSION_ID, { type: 'kosha:remove_from_sources' }); // { ok, detail }
chrome.runtime.sendMessage(EXTENSION_ID, { type: 'kosha:removal_status' });      // { ok, removal }
```

`removal` is `{ status: running|finished|stopped|aborted|error, total, done, removed, already_gone, failed, deferred, unreported, note }`.

## Server endpoints used

| Call | Body | Expected answer |
|---|---|---|
| `POST /api/captures` | the envelope | 2xx; `{ capture_id, items_new, items_updated, items_unchanged }` |
| `GET /api/health` | | 2xx when the token is accepted, 401 when not |
| `GET /api/removals/pending` | | `[{ item_id, source: "x"\|"linkedin", source_id, url }]`; `source_id` is the post id on X and the `urn:li:activity:…` urn on LinkedIn |
| `POST /api/removals/result` | `{ item_id, status: "removed"\|"already_gone"\|"failed", detail }` | 2xx |

All carry `Authorization: Bearer <token>`. A capture the server answers with 401, 403, 408, 429, 5xx or no answer is kept and retried with a growing delay (5 seconds up to 15 minutes); any other 4xx marks it as refused, and it is kept but not sent again.

## Layout

```
src/config.ts            constants: names, URLs, pacing, limits
src/background.ts        service worker: outbox, connection test, removal run
src/outbox.ts            IndexedDB outbox with retry
src/envelope.ts          capture envelope
src/panel.ts             the floating panel
src/x/                   inpage.ts (network hook), content.ts (bridge, backfill), backfill.ts, match.ts, unbookmark.ts
src/linkedin/            selectors.ts, extract.ts, unsave.ts, content.ts
src/removal/queue.ts     removal order, pacing, stop rules
src/popup/               popup
```

## Credit

The network hook and bookmark matching follow [twitter-web-exporter](https://github.com/prinsss/twitter-web-exporter) by prinsss (MIT). See `NOTICE`.
