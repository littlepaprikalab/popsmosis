import type { Metadata } from 'next';
import Link from 'next/link';
import PageContainer from '@/components/PageContainer';
import { Wordmark } from '@/components/Wordmark';
import { DropletPill, Marked, Pill, SongChip, ink } from '@/components/Highlights';
import { HeartIcon, PlayIcon } from '@heroicons/react/24/solid';

export const metadata: Metadata = {
  title: 'About',
  description: 'I\'m Samantha, and I make POPsmosis. Here are the other apps and sites I\'ve made.',
};

const card = { background: 'var(--bg-secondary)', border: '1px solid var(--border)' } as const;

interface Shot { src: string; alt: string }

interface App {
  name: string;
  tagline: string;
  about: string;
  icon: string;
  appStore: string;
  googlePlay: string;
  shots: Shot[];
}

interface Site {
  name: string;
  url: string;
  tagline: string;
  about: string;
  shot: Shot;
}

// The screenshots are served from public/about/ rather than the stores, so reading this page asks nothing of Apple
// or Google (Privacy Policy, section 10). The store links carry no tracking parameters for the same reason.
const APPS: App[] = [
  {
    name: 'Wisteria Reader',
    tagline: 'Learn a language by reading what you love',
    about: 'Phrasebooks teach you to order coffee; fluency comes from reading things you care about. Wisteria turns '
      + 'any article, PDF or e-book into a bilingual lesson: each sentence sits beside its translation and lights up as '
      + 'it is read aloud in both languages. Over 23 languages, and it works offline.',
    icon: '/about/wisteria-icon.webp',
    appStore: 'https://apps.apple.com/app/wisteria-reader/id6753122777',
    googlePlay: 'https://play.google.com/store/apps/details?id=com.spicygingerlabs.echoreader',
    shots: [
      { src: '/about/wisteria-1.webp', alt: 'A Korean story with its French translation underneath, one sentence highlighted in both' },
      { src: '/about/wisteria-2.webp', alt: 'A Chinese story beside its French translation, with the list of languages to choose from open' },
      { src: '/about/wisteria-3.webp', alt: 'Importing a web page, pasted text, a PDF or an EPUB to read' },
    ],
  },
  {
    name: 'Writeline',
    tagline: 'A journal that asks for one line',
    about: 'Most journals ask for a page, so most get abandoned. Writeline asks for one line, like "ramen with Justin '
      + 'at Ippudo", which takes about ten seconds. It files each line for you, remembers the people and places in it, '
      + 'and keeps your habits checked off beside it. Keep it up for a few months and you have a record of your actual '
      + 'life.',
    icon: '/about/writeline-icon.webp',
    appStore: 'https://apps.apple.com/app/writeline-one-line-life-log/id6814636848',
    googlePlay: 'https://play.google.com/store/apps/details?id=com.spicygingerlabs.logline',
    shots: [
      { src: '/about/writeline-1.webp', alt: 'A day of one-line entries, each labeled moved, ate, worked or slept' },
      { src: '/about/writeline-2.webp', alt: 'A year of days as a grid of colored dots' },
      { src: '/about/writeline-3.webp', alt: 'A month broken down by the kind of thing each line was about' },
    ],
  },
  {
    name: 'Hummingbird Biofeedback',
    tagline: 'Watch your nervous system settle, live',
    about: 'Your heart-rate strap (Whoop, Polar H10, Garmin) already broadcasts every beat. Hummingbird reads it and '
      + 'shows your HRV, vagal depth and coherence live, while you hum along to a guided pitch, breathe or meditate, so '
      + 'you can see what actually calms you down. No account and no server.',
    icon: '/about/hummingbird-icon.webp',
    appStore: 'https://apps.apple.com/app/hummingbird-biofeedback/id6762854409',
    googlePlay: 'https://play.google.com/store/apps/details?id=com.spicygingerlabs.hummingbird',
    shots: [
      { src: '/about/hummingbird-1.webp', alt: 'Live heart rate, HRV, vagal depth and coherence, with the humming programs below' },
      { src: '/about/hummingbird-2.webp', alt: 'A humming session: the pitch target and the heart rate it pulls on' },
      { src: '/about/hummingbird-3.webp', alt: 'Resting HRV and heart rate over a day' },
    ],
  },
];

const SITES: Site[] = [
  {
    name: 'SAT Sprints',
    url: 'https://www.satsprints.com',
    tagline: 'Free AP exam and Digital SAT prep',
    about: 'Study guides for all 15 AP subjects, free with no paywall or trial, plus bite-size daily SAT lessons, a '
      + 'daily Blitz quiz with a leaderboard and 20 full-length practice exams.',
    shot: { src: '/about/satsprints.webp', alt: 'The SAT Sprints home page' },
  },
  {
    name: 'Total Sparks AI',
    url: 'https://totalsparksai.com',
    tagline: 'A social network of AI agents, and you can read their minds',
    about: 'AI agents with their own personalities meet, pitch each other ideas and get turned down, and every public '
      + 'move comes with a private thought only you can read. Watch and vote, adopt an agent, or take the 32-question '
      + 'personality analysis.',
    shot: { src: '/about/totalsparks.webp', alt: 'The Total Sparks AI home page' },
  },
];

/** Who makes POPsmosis, then the other apps and sites from the same maker, with links to get them. */
export default function AboutPage() {
  return (
    <PageContainer maxWidth="4xl" className="py-8 px-4">
      <header className="mb-10">
        <h1 className="text-2xl font-bold tracking-wider mb-4" style={{ color: 'var(--text-primary)' }}>
          Hi, I&rsquo;m Samantha
        </h1>
        <div className="flex flex-col gap-3 text-sm leading-relaxed max-w-2xl" style={{ color: 'var(--text-secondary)' }}>
          <p>I love vibe coding and bringing the things I care about to life.</p>
          <p>
            <Wordmark size="sm" /> started with a problem of my own: I wanted
            to <Marked color="var(--accent-yellow)">automate</Marked> making educational song videos
            for <a href="#sat-sprints" className="underline" style={{ color: 'var(--accent-blue)' }}>SAT
            Sprints</a>, the AP and SAT prep site I built for{' '}
            {/* One piece, so the heart never wraps away from "my kid" */}
            <span className="whitespace-nowrap">
              <strong style={{ color: 'var(--text-primary)' }}>my kid</strong>{' '}
              <HeartIcon aria-hidden="true" className="inline-block"
                style={{ width: '1em', height: '1em', verticalAlign: '-0.15em', color: 'var(--accent-red)' }} />.
            </span>{' '}
            Making them was
            a <Marked color="var(--accent-pink)">pain in the tush</Marked>, with pictures that came out wrong and
            content that needed checking line by line, and the <Pill color="var(--accent-purple)">#metahackathon</Pill> was
            a good excuse to fix that. Ask for any topic and an AI teacher makes you
            a <span className="whitespace-nowrap"><DropletPill />,</span> a short lesson
            with a <Marked color="var(--accent-green)">narrated video</Marked> and
            a <span className="whitespace-nowrap"><SongChip />.</span> It
            checks <strong style={{ color: 'var(--text-primary)' }}>every line and every picture</strong> first, and
            shares the droplet on the <Link href="/droplets" className="underline"
            style={{ color: 'var(--accent-blue)' }}>Droplets tab</Link> for anyone to learn from. I
            hope <Wordmark size="sm" /> becomes where people go when the textbook isn&rsquo;t clicking, and that they can trust every word of the song.
          </p>
          {/* Try it before the other products: watching needs no account, asking does */}
          <div className="my-3 rounded-lg p-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between" style={card}>
            <p className="font-bold" style={{ color: 'var(--text-primary)' }}>Stuck on a topic?<br />Turn it into a song.</p>
            <div className="flex flex-wrap gap-2 shrink-0">
              <Link href="/droplets" className="inline-flex items-center gap-1.5 px-4 py-2 rounded text-xs font-bold uppercase tracking-wider transition-all hover:brightness-110"
                style={{ background: 'var(--accent-brand)', color: 'var(--accent-brand-text)' }}>
                <PlayIcon aria-hidden="true" className="w-3.5 h-3.5" />Watch a droplet
              </Link>
              <Link href="/learn" className="inline-block px-4 py-2 rounded text-xs font-bold uppercase tracking-wider transition-colors hover-border"
                style={{ border: '1px solid var(--accent-brand)', color: 'var(--accent-brand)' }}>
                Ask for a topic
              </Link>
            </div>
          </div>
          <p>Might as well show you what else I&rsquo;ve made over the past year and a half, lol.</p>
        </div>
      </header>

      <section aria-labelledby="apps-title" className="mb-12">
        <SectionTitle id="apps-title">Mobile apps</SectionTitle>
        <div className="flex flex-col gap-6">
          {APPS.map((app) => <AppCard key={app.name} app={app} />)}
        </div>
      </section>

      <section aria-labelledby="web-title">
        <SectionTitle id="web-title">On the web</SectionTitle>
        <div className="grid gap-6 md:grid-cols-2">
          {SITES.map((site) => <SiteCard key={site.name} site={site} />)}
        </div>
      </section>
    </PageContainer>
  );
}

function SectionTitle({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <h2 id={id} className="text-xs font-bold uppercase tracking-wider mb-4" style={{ color: ink('var(--accent-yellow)', 55) }}>
      {children}
    </h2>
  );
}

function AppCard({ app }: { app: App }) {
  return (
    <article id={slug(app.name)} className="rounded-lg p-4 sm:p-5 lg:flex lg:gap-6 lg:items-start scroll-mt-18" style={card}
      aria-labelledby={`${slug(app.name)}-title`}>
      <div className="lg:flex-1 min-w-0">
        <div className="flex items-center gap-3 mb-3">
          <img src={app.icon} alt="" width={48} height={48} className="rounded-xl shrink-0" />
          <div className="min-w-0">
            <h3 id={`${slug(app.name)}-title`} className="font-bold" style={{ color: 'var(--text-primary)' }}>{app.name}</h3>
            <p className="text-xs" style={{ color: ink('var(--accent-purple)', 85) }}>{app.tagline}</p>
          </div>
        </div>
        <p className="text-sm leading-relaxed mb-4" style={{ color: 'var(--text-secondary)' }}>{app.about}</p>
        <div className="flex flex-wrap gap-2 mb-4 lg:mb-0">
          <OutLink href={app.appStore} label={`${app.name} on the App Store`}>App Store</OutLink>
          <OutLink href={app.googlePlay} label={`${app.name} on Google Play`}>Google Play</OutLink>
        </div>
      </div>
      {/* On a phone the screenshots scroll sideways; from sm up all three fit, and from lg they sit beside the text */}
      <ul className="flex gap-3 overflow-x-auto snap-x snap-mandatory pb-1 lg:shrink-0" aria-label={`${app.name} screenshots`}>
        {app.shots.map((shot) => (
          <li key={shot.src} className="snap-start shrink-0 w-40 sm:w-44 lg:w-36">
            <img src={shot.src} alt={shot.alt} width={400} height={869} loading="lazy"
              className="block w-full h-auto rounded-lg" style={{ border: '1px solid var(--border)' }} />
          </li>
        ))}
      </ul>
    </article>
  );
}

function SiteCard({ site }: { site: Site }) {
  const host = new URL(site.url).hostname.replace(/^www\./, '');
  return (
    <article id={slug(site.name)} className="rounded-lg overflow-hidden flex flex-col scroll-mt-18" style={card}
      aria-labelledby={`${slug(site.name)}-title`}>
      <img src={site.shot.src} alt={site.shot.alt} width={960} height={600} loading="lazy"
        className="block w-full h-auto" style={{ borderBottom: '1px solid var(--border)' }} />
      <div className="p-4 sm:p-5 flex flex-col flex-1">
        <h3 id={`${slug(site.name)}-title`} className="font-bold" style={{ color: 'var(--text-primary)' }}>{site.name}</h3>
        <p className="text-xs mb-3" style={{ color: ink('var(--accent-purple)', 85) }}>{site.tagline}</p>
        <p className="text-sm leading-relaxed mb-4 flex-1" style={{ color: 'var(--text-secondary)' }}>{site.about}</p>
        <div>
          <OutLink href={site.url} label={`Visit ${site.name} at ${host}`}>{host}</OutLink>
        </div>
      </div>
    </article>
  );
}

function OutLink({ href, label, children }: { href: string; label: string; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" aria-label={label}
      className="inline-block px-4 py-2 rounded text-xs font-bold uppercase tracking-wider transition-all hover:brightness-110"
      style={{ background: 'var(--accent-brand)', color: 'var(--accent-brand-text)' }}>
      {children} ↗
    </a>
  );
}

const slug = (name: string) => name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
