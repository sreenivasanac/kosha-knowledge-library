/**
 * The public page a visitor sees before signing in. It asks the server for
 * nothing beyond the public config, and its text is written here, in one place.
 * Images are crops of docs/screenshots, copied to public/landing.
 */
import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { ArrowDown, Search, Star } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { LEGACY_CONFIG, publicConfigQuery } from './api';
import { APP_NAME, AUTHOR, HERO_LINE, LICENSE_NAME, LINKS, MODES, REPO_URL } from './constants';
import { KeyTour } from './Onboarding';
import { tokenize } from './query';
import { Logo } from './ui';

/** The query the Search block shows, and what each part of it means. Chips come from the app's own tokeniser. */
const SAMPLE_QUERY = 'from:karpathy has:video #AI/RAG "prompt caching"';
const SAMPLE_MEANING = ['posts by this author', 'that have a video', 'under this tag, or a tag below it', 'with these words, in this order'];

const MCP_COMMAND = 'claude mcp add --transport http kosha \\\n  https://your-server/mcp \\\n  --header "Authorization: Bearer $KOSHA_API_TOKEN"';
const PIPELINE = 'where is:untagged\n| jev "Is this post about retrieval-augmented generation (RAG)?" >= 0.9\n| tag "AI/RAG & Search"';
const QUICK_START = `git clone ${REPO_URL}\ncd kosha-knowledge-library\ncp .env.example .env     # set the three required values\ndocker compose up -d`;

/** The keys the Keyboard block shows, by their ids in the keymap registry. */
const LANDING_KEYS = ['move.next', 'move.prev', 'item.open', 'item.tag', 'select.toggle', 'item.archive', 'item.star', 'undo', 'search.focus', 'palette.open', 'go.untagged', 'help'];

const FAQ: { q: string; a: ReactNode }[] = [
  {
    q: 'Does it need my X or LinkedIn password?',
    a: 'No. The extension works inside your own browser, on pages you are already signed in to, and reads what those sites show you. It never sees a password. It does browse on your behalf, which may be against those sites’ terms, and a long backfill can get an account rate-limited or restricted.',
  },
  { q: 'Where is my data?', a: 'On the server you run: posts in a Postgres database, images and videos in a folder on its disk or in an S3-compatible bucket you choose.' },
  { q: 'Can I get it out?', a: 'Yes, at any time. Export everything, or the result of any search, as CSV, bookmarks HTML or JSON. It is also your database: nothing stops you reading it directly.' },
  { q: 'Does it work without AI keys?', a: 'Yes. Capture, keyword search, tags, lists and every keyboard shortcut work with no key. A Voyage key adds search by meaning, a TypeSafe key adds yes/no checks and automatic labelling, and an OpenAI key adds Ask and text from images and video.' },
  { q: 'Does it work with Claude Code and other agents?', a: 'Yes. Kosha serves an MCP endpoint over HTTP with a bearer token. Claude Code connects with one command; any other MCP client works the same way.' },
  { q: 'Is there a hosted version?', a: 'No. Kosha is self-host only. It is free and there is nothing to subscribe to.' },
];

function Code({ children, label }: { children: string; label: string }) {
  // Said on the button itself: the app's toasts belong to the signed-in app and would ask the server who is here.
  const [copied, setCopied] = useState(false);
  return (
    <figure className="overflow-hidden rounded-lg border border-line bg-sunken">
      <figcaption className="flex items-center justify-between border-b border-line px-3 py-1.5 text-xs text-ink-2">
        {label}
        <button className="rounded px-1.5 py-0.5 font-medium text-accent hover:underline" onClick={() => navigator.clipboard.writeText(children).then(() => setCopied(true), () => undefined)}>
          {copied ? 'Copied' : 'Copy'}
        </button>
      </figcaption>
      <pre className="whitespace-pre-wrap break-words px-3 py-3 font-mono text-[0.8rem] leading-relaxed">{children}</pre>
    </figure>
  );
}

/** A screenshot in a plain frame. Sizes are given so the page does not jump while it loads. */
function Shot({ src, alt, width, height, eager }: { src: string; alt: string; width: number; height: number; eager?: boolean }) {
  return <img src={src} alt={alt} width={width} height={height} loading={eager ? 'eager' : 'lazy'} decoding="async" className="h-auto w-full rounded-xl border border-line shadow-pop" />;
}

/** One feature: words on one side, something to look at on the other. `flip` swaps the sides on a wide screen. */
function Feature({ title, points, children, flip }: { title: string; points: ReactNode[]; children: ReactNode; flip?: boolean }) {
  return (
    <section className="grid items-center gap-x-14 gap-y-6 py-12 lg:grid-cols-12 lg:py-16">
      <div className={`lg:col-span-5 ${flip ? 'lg:order-2' : ''}`}>
        <h3 className="text-2xl font-semibold tracking-tight sm:text-[1.75rem]">{title}</h3>
        <ul className="mt-4 space-y-2.5 text-ink-2">
          {points.map((p, i) => (
            <li key={i} className="flex gap-3">
              <span aria-hidden className="mt-[0.6em] h-1.5 w-1.5 shrink-0 bg-mark" />
              <span>{p}</span>
            </li>
          ))}
        </ul>
      </div>
      <div className="min-w-0 lg:col-span-7">{children}</div>
    </section>
  );
}

function CaptureVisual() {
  const sources = ['X bookmarks', 'LinkedIn saved posts', 'Raindrop, browser bookmarks or CSV file'];
  return (
    <div className="grid items-center gap-3 rounded-xl border border-line bg-surface p-5 sm:grid-cols-[1fr_auto_1fr]">
      <ul className="space-y-2">
        {sources.map((s, i) => (
          <li key={s} className="rounded-md border border-line bg-bg px-3 py-2 text-sm">
            <span className="block font-medium">{s}</span>
            <span className="text-xs text-ink-3">{i < 2 ? 'through the extension, in your browser' : 'through Import'}</span>
          </li>
        ))}
      </ul>
      <ArrowDown aria-hidden className="mx-auto text-ink-3 sm:-rotate-90" />
      <div className="rounded-md border-2 border-accent bg-accent-soft px-4 py-4">
        <p className="flex items-center gap-2 font-semibold">
          <Logo size={20} /> Your {APP_NAME} server
        </p>
        <ul className="mt-2 space-y-1 text-sm text-ink-2">
          <li>Text, author, links and counts</li>
          <li>A copy of the images</li>
          <li>The raw capture, so parsing can be redone</li>
        </ul>
      </div>
    </div>
  );
}

/** A sample of a tag tree, drawn the way the sidebar draws one: a dot, a name, a count. */
const SAMPLE_TREE: [depth: number, name: string, count: string][] = [
  [0, 'AI', '3,513'],
  [1, 'Agents', '859'],
  [1, 'RAG & Search', '564'],
  [1, 'Prompting & Evals', '230'],
  [0, 'Coding with AI', '1,088'],
  [0, 'Design & Frontend', '312'],
  [0, 'Career', '208'],
];
function TagTreeVisual() {
  return (
    <ul aria-label="A sample tag tree" className="rounded-lg border border-line bg-surface p-2 text-sm">
      {SAMPLE_TREE.map(([depth, name, count]) => (
        <li key={name} className={`flex items-center gap-2 rounded-md px-2 py-1 ${name === 'RAG & Search' ? 'bg-accent-soft font-semibold text-accent' : 'text-ink-2'}`} style={{ paddingLeft: 8 + depth * 14 }}>
          <span aria-hidden className="h-2 w-2 shrink-0 rounded-full bg-ink-3" />
          <span className="min-w-0 flex-1 truncate">{name}</span>
          <span className="text-xs tabular-nums text-ink-3">{count}</span>
        </li>
      ))}
    </ul>
  );
}

function SearchVisual() {
  const tokens = tokenize(SAMPLE_QUERY);
  return (
    <div className="space-y-3 rounded-xl border border-line bg-surface p-4 sm:p-5">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex h-10 min-w-0 flex-1 items-center gap-2 rounded-md border border-accent px-3 outline-2 outline-accent-soft">
          <Search size={16} aria-hidden className="shrink-0 text-ink-3" />
          <span className="truncate font-mono text-sm">{SAMPLE_QUERY}</span>
        </div>
        <div aria-hidden className="flex rounded-md border border-line p-0.5 text-xs">
          {MODES.map((m, i) => (
            <span key={m} className={`rounded px-2 py-1 capitalize ${i === 0 ? 'bg-accent-soft font-semibold text-accent' : 'text-ink-2'}`}>
              {m}
            </span>
          ))}
        </div>
      </div>
      <dl className="grid gap-y-2 text-sm sm:grid-cols-[auto_1fr] sm:gap-x-4">
        {tokens.map((t, i) => (
          <div key={t.start} className="contents">
            <dt>
              <span className="inline-block rounded bg-accent-soft px-2 py-0.5 font-mono text-[0.8rem] text-accent">{t.raw}</span>
            </dt>
            <dd className="text-ink-2 max-sm:-mt-1 max-sm:mb-1">{SAMPLE_MEANING[i]}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export function Landing() {
  const config = useQuery(publicConfigQuery).data ?? LEGACY_CONFIG;
  const nav = 'rounded px-2 py-1 text-sm text-ink-2 hover:text-ink';
  const wrap = 'mx-auto w-full max-w-6xl px-4 sm:px-6';
  const demo = config.demo_url;
  return (
    <div id="top">
      <header className="sticky top-0 z-20 border-b border-line bg-bg/90 backdrop-blur">
        <nav aria-label="Page" className={`${wrap} flex h-14 items-center gap-1`}>
          <a href="#top" className="mr-auto flex items-center gap-2 text-lg font-semibold tracking-tight">
            <Logo /> {APP_NAME}
          </a>
          <a href="#features" className={`${nav} max-md:hidden`}>
            Features
          </a>
          <a href="#self-host" className={`${nav} max-md:hidden`}>
            Self-host
          </a>
          {demo && (
            <a href={demo} className={`${nav} max-md:hidden`}>
              Demo
            </a>
          )}
          <a href={LINKS.repo} className={nav}>
            GitHub
          </a>
          <Link to="/login" className="btn ml-1">
            Sign in
          </Link>
        </nav>
      </header>

      <main>
        <section className={`${wrap} pb-10 pt-12 sm:pt-16`}>
          {/* The turmeric ribbon is how the app marks the item in focus; here it marks the one line that matters. */}
          <div className="max-w-4xl border-l-4 border-mark pl-5 sm:pl-7">
            <h1 className="text-[clamp(2.1rem,5.4vw,3.9rem)] font-semibold leading-[1.05] tracking-[-0.03em]">{HERO_LINE}</h1>
            <p className="mt-5 max-w-2xl text-lg text-ink-2">
              {APP_NAME} keeps a copy of your X bookmarks and LinkedIn saved posts, sorts them into a tag tree, and finds them again by keyword or by meaning. It is free and open source.
            </p>
            <div className="mt-7 flex flex-wrap gap-3">
              {demo && (
                <a href={demo} className="btn btn-primary h-11 px-5 text-base">
                  Try the demo
                </a>
              )}
              <a href="#self-host" className={`btn h-11 px-5 text-base ${demo ? '' : 'btn-primary'}`}>
                Self-host it
              </a>
              {/* A plain link to the repository page, where the Star button is; no script from GitHub is loaded. */}
              <a href={LINKS.repo} className="btn h-11 gap-2 px-5 text-base">
                <Star size={16} aria-hidden /> Star on GitHub
              </a>
            </div>
          </div>
          <div className="mt-10 sm:mt-12">
            <picture>
              <source srcSet="/landing/app.webp" type="image/webp" />
              <Shot eager src="/landing/app.png" alt={`The ${APP_NAME} library: a search with qualifier chips, results as compact rows, and the tag tree in the sidebar`} width={1440} height={900} />
            </picture>
          </div>
        </section>

        <section aria-label="In short" className="border-y border-line bg-surface">
          <ul className={`${wrap} flex flex-wrap gap-y-1 py-4 text-sm font-medium max-sm:flex-col sm:divide-x sm:divide-line`}>
            {['Open source', `${LICENSE_NAME} licence`, 'Self-hosted', 'Free', 'Your data stays on your server'].map((fact) => (
              <li key={fact} className="sm:px-5 sm:first:pl-0">
                {fact}
              </li>
            ))}
          </ul>
        </section>

        <h2 id="features" className="sr-only scroll-mt-14">
          Features
        </h2>
        <div className={`${wrap} divide-y divide-line`}>
          <Feature title="Save the way you already do" points={['A Chrome extension copies your X bookmarks and LinkedIn saved posts while you look at them.', 'Backfill scrolls through years of old bookmarks for you, slowly.', 'Import a Raindrop export, a browser bookmarks file or any CSV of links.']}>
            <CaptureVisual />
          </Feature>
          <Feature flip title="Find the post you half remember" points={['Narrow a search with qualifiers, as in Gmail: author, tag, media, date, likes.', 'Keyword and meaning are combined, so a post turns up even when it uses other words than you typed.', 'Exact phrases, prefixes, fuzzy words and regular expressions when you need them.']}>
            <SearchVisual />
          </Feature>
          <Feature
            title="A tag tree, kept by you or by your agent"
            points={[
              'Tags nest, like AI › Agents, and a post can sit under several. There are no folders to choose between.',
              <>
                The MCP server gives an agent one <code className="font-mono text-[0.9em] text-ink">query</code> tool that chains search, yes/no checks and tagging.
              </>,
              'A tag an agent invents stays “proposed” until you approve it.',
            ]}
          >
            <div className="grid items-start gap-3 sm:grid-cols-[13rem_minmax(0,1fr)]">
              <TagTreeVisual />
              <div className="space-y-3">
                <Code label="Connect Claude Code">{MCP_COMMAND}</Code>
                <Code label="One pipeline: find, check, tag">{PIPELINE}</Code>
              </div>
            </div>
          </Feature>
          <Feature flip title="Hands on the keyboard" points={['Move, tag, select and archive without reaching for the mouse.', 'The command palette runs any action by name.', 'Every key can be changed in Settings.']}>
            <div className="rounded-xl border border-line bg-surface p-4 sm:p-5">
              <KeyTour ids={LANDING_KEYS} />
            </div>
          </Feature>
          <Feature title="Ask your library a question" points={['Kosha searches your saved posts in a few steps and answers from what it found.', 'Each statement links to the post it came from.', 'You see how it searched and what the answer cost.']}>
            <Shot src="/landing/ask.webp" alt="Ask: a question, an answer with numbered citations, and the search steps behind it" width={720} height={640} />
          </Feature>
        </div>

        <section className="border-y border-line bg-accent-soft">
          <div className={`${wrap} grid gap-x-14 gap-y-6 py-14 lg:grid-cols-12`}>
            <h2 className="text-3xl font-semibold tracking-tight lg:col-span-5">Your data stays yours</h2>
            <div className="space-y-4 lg:col-span-7">
              <p className="text-lg">Export everything, or any search, as CSV, bookmarks HTML or JSON whenever you like.</p>
              <p className="text-lg">Nothing leaves your server except the AI calls you choose to turn on. With no keys set, {APP_NAME} talks to nobody.</p>
            </div>
          </div>
        </section>

        <section className={`${wrap} grid gap-x-14 gap-y-6 py-14 lg:grid-cols-12`}>
          <h2 className="text-3xl font-semibold tracking-tight lg:col-span-5">Questions</h2>
          <div className="divide-y divide-line border-y border-line lg:col-span-7">
            {FAQ.map((f) => (
              <details key={f.q} className="group py-1">
                <summary className="flex cursor-pointer list-none items-center gap-3 py-3 font-medium [&::-webkit-details-marker]:hidden">
                  <span className="flex-1">{f.q}</span>
                  <span aria-hidden className="text-xl leading-none text-ink-3 transition-transform group-open:rotate-45">
                    +
                  </span>
                </summary>
                <p className="max-w-prose pb-4 text-ink-2">{f.a}</p>
              </details>
            ))}
          </div>
        </section>

        <section id="self-host" className="scroll-mt-14 border-t border-line bg-surface">
          <div className={`${wrap} grid gap-x-14 gap-y-6 py-14 lg:grid-cols-12`}>
            <div className="lg:col-span-5">
              <h2 className="text-3xl font-semibold tracking-tight">Run it yourself</h2>
              <p className="mt-3 text-ink-2">Docker with Compose is all you need. It starts two containers: Postgres, and {APP_NAME} with its web app and MCP endpoint.</p>
              <p className="mt-3 text-ink-2">
                Then open <code className="font-mono text-[0.9em] text-ink">http://localhost:8080</code> and create the admin account.
              </p>
            </div>
            <div className="min-w-0 space-y-3 lg:col-span-7">
              <Code label="Quick start">{QUICK_START}</Code>
              <a href={LINKS.readme} className="btn">
                Read the full guide in the README
              </a>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-line">
        <div className={`${wrap} flex flex-wrap items-center gap-x-5 gap-y-2 py-6 text-sm text-ink-2`}>
          <span className="flex items-center gap-2 font-semibold text-ink">
            <Logo size={18} /> {APP_NAME}
          </span>
          <span>
            Made by {AUTHOR.name}
          </span>
          <a className="hover:text-ink hover:underline" href={AUTHOR.github}>
            GitHub
          </a>
          <a className="hover:text-ink hover:underline" href={AUTHOR.linkedin}>
            LinkedIn
          </a>
          <a className="hover:text-ink hover:underline" href={AUTHOR.x}>
            X
          </a>
          <span className="flex-1" />
          <a className="hover:text-ink hover:underline" href={LINKS.repo}>
            Source code
          </a>
          <a className="hover:text-ink hover:underline" href={LINKS.license}>
            {LICENSE_NAME} licence
          </a>
        </div>
      </footer>
    </div>
  );
}
