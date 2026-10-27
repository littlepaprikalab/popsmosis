import type { Metadata } from 'next';
import Link from 'next/link';
import { LegalPage, Section, Contact } from '@/components/LegalPage';
import { OPERATOR_NAME, OPERATOR_LOCATION, MIN_AGE, ATTACHMENT_RETENTION_DAYS } from '@/lib/legal';

export const metadata: Metadata = {
  title: 'Privacy Policy',
  description: 'What Popsmosis publishes, what it keeps private, who helps run it, and how to delete your data.',
};

/**
 * The Privacy Policy. What it promises is what the code does: lib/privacy.ts in the API (who reads what, and taking
 * a request back), DELETE /api/auth/me (deleting an account), and lib/droplet.ts (what the Droplets tab sends). Keep
 * them in step, and move LAST_UPDATED in lib/legal.ts when this page changes.
 */
export default function PrivacyPage() {
  return (
    <LegalPage
      title="Privacy Policy"
      summary={
        <ul>
          <li><strong>The lessons are public.</strong> Every droplet the AI teacher makes is published on the{' '}
            <Link href="/droplets">Droplets tab</Link> for anyone to watch, with no account needed.</li>
          <li><strong>You are private.</strong> Your name, email address, photo, the words you type, the files you send
            and your follow-up questions are never published. Only you, the AI teacher and the people who run
            Popsmosis can see them.</li>
          <li><strong>You are in control.</strong> You can delete a request, or your whole account, at any time. The
            lesson stays public, with nothing linking it to you.</li>
          <li><strong>No ads, no tracking, no selling.</strong> We don&rsquo;t sell your data, show ads, use analytics or
            tracking cookies, or train AI models on what you send.</li>
        </ul>
      }
    >
      <Section id="who" title="1. Who we are">
        <p>
          Popsmosis is a free learning site. You ask for a topic and an AI teacher makes you a <em>droplet</em>: a short,
          fact-checked lesson with a narrated video and a song. Popsmosis is run by <strong>{OPERATOR_NAME}</strong>,
          based in {OPERATOR_LOCATION} (&ldquo;we&rdquo;, &ldquo;us&rdquo;), which is responsible for your personal data
          as this policy describes.
          You can reach us at <Contact />.
        </p>
      </Section>

      <Section id="public-private" title="2. What is public and what is private">
        <div className="legal-split">
          <div>
            <h3 style={{ color: 'var(--accent-green)' }}>Public: anyone can see it</h3>
            <ul>
              <li>The lesson: its text, diagrams, narration, song and lyrics</li>
              <li>A title naming the subject, such as &ldquo;AP Biology: ATP synthesis&rdquo;</li>
              <li>When the lesson was made</li>
            </ul>
          </div>
          <div>
            <h3 style={{ color: 'var(--accent-purple)' }}>Private: you, the AI teacher and our admins</h3>
            <ul>
              <li>Your name, email address and Google profile photo</li>
              <li>Your requests, in your own words</li>
              <li>The files you attach</li>
              <li>Your follow-up questions and the teacher&rsquo;s answers to them</li>
            </ul>
          </div>
        </div>
        <p>
          A lesson is written for anyone learning the topic, not about you. It is shaped by your request, though (the
          course, the level, a song style you asked for), so keep personal details out of what you type. Never include
          anyone else&rsquo;s. Popsmosis refuses requests that contain an email address or a phone number, but it
          can&rsquo;t catch everything. If a lesson ever says something that identifies you or someone else, tell us and
          we&rsquo;ll take it down.
        </p>
        <p>
          Our admins are the few people who run Popsmosis. They can see requests and follow-ups so they can fix problems
          and keep the site safe, and they don&rsquo;t share them.
        </p>
      </Section>

      <Section id="collect" title="3. What we collect">
        <dl>
          <dt>When you sign in with Google</dt>
          <dd>Your name, email address, profile photo and Google account ID. We never see your Google password, and we
            don&rsquo;t ask for access to anything else in your Google account.</dd>
          <dt>What you send</dt>
          <dd>Your requests, the files you attach (such as photos of your notes) and your follow-up questions.</dd>
          <dt>What the teacher makes for you</dt>
          <dd>The lessons, videos and replies to your follow-ups.</dd>
          <dt>Technical information</dt>
          <dd>A cookie that keeps you signed in (<a href="#cookies">section 10</a>). Our hosting provider&rsquo;s server
            logs, which record IP addresses, the addresses requested, times and errors. IP addresses are also held
            briefly in memory to stop any one visitor overloading the site.</dd>
        </dl>
        <p>
          We don&rsquo;t collect payment details, your location, your contacts, or advertising or analytics identifiers.
        </p>
      </Section>

      <Section id="use" title="4. How we use it">
        <ul>
          <li><strong>To make your lessons.</strong> The AI teacher reads your request, your files and your follow-ups to
            write the lesson, check it and answer you.</li>
          <li><strong>To run your account.</strong> Signing you in and showing you your requests.</li>
          <li><strong>To keep Popsmosis safe and working.</strong> Limits on how much one person can ask, preventing
            abuse, fixing bugs and taking down lessons that break our <Link href="/terms">Terms</Link>.</li>
          <li><strong>To publish the lessons.</strong> Each finished lesson goes on the Droplets tab, without anything
            about you.</li>
        </ul>
        <p>
          We don&rsquo;t sell your data, use it for advertising, or use it to train AI models. If you are in the European
          Economic Area or the UK, the legal bases are: providing the service you asked for (contract); keeping it secure,
          preventing abuse and publishing the lessons (our legitimate interests); and meeting legal obligations.
        </p>
      </Section>

      <Section id="providers" title="5. Who helps us run it">
        <p>We use these companies to run Popsmosis. Each one gets only what its job needs.</p>
        <dl>
          <dt>Google</dt>
          <dd>Sign-in. Google&rsquo;s sign-in page applies{' '}
            <a href="https://policies.google.com/privacy" rel="noopener noreferrer" target="_blank">Google&rsquo;s privacy policy</a>.</dd>
          <dt>Render</dt>
          <dd>Hosts the website and its server, in the United States. Everything passes through it, and it keeps the
            server logs.</dd>
          <dt>Supabase</dt>
          <dd>Our database and video storage: your account, requests, files and follow-ups, and the public videos.</dd>
          <dt>Anthropic (Claude)</dt>
          <dd>The AI teacher. It reads your requests, files and follow-ups to write, check and explain the lessons.</dd>
          <dt>Meta</dt>
          <dd>Only if we switch the lesson writer to Meta&rsquo;s AI models. It would then receive the same as
            Anthropic.</dd>
          <dt>ElevenLabs, or Google&rsquo;s Lyria</dt>
          <dd>The narration and the songs. They receive the lesson script, the lyrics and the song&rsquo;s style, which can
            come from a style you asked for. Never your name or account.</dd>
          <dt>Inkmory</dt>
          <dd>A public journal where the AI teacher writes about its own work. It gets the lesson&rsquo;s subject only,
            never anything about you.</dd>
        </dl>
        <p>
          While it makes a lesson, the AI teacher keeps working files, which can include your request and your files, on
          the computer that runs it, so it can retry a step that failed. They are never published
          (see <a href="#retention">section 6</a> for how long they stay).
        </p>
        <p>
          We may also disclose information if the law requires it, to protect someone&rsquo;s safety or rights, or if
          Popsmosis passes to a new operator, who would then be bound by this policy.
        </p>
      </Section>

      <Section id="retention" title="6. How long we keep it">
        <ul>
          <li><strong>Your account:</strong> until you delete it.</li>
          <li><strong>Your requests and follow-ups:</strong> until you delete them or your account.</li>
          <li><strong>Your files:</strong> each file is deleted {ATTACHMENT_RETENTION_DAYS} days after you send it. Its
            name and size stay with your request, so it still shows what you sent.</li>
          <li><strong>Lessons and videos:</strong> published for as long as Popsmosis runs, unless they are taken down.</li>
          <li><strong>Server logs and backups:</strong> kept by our providers for a limited time, after which they
            expire.</li>
          <li><strong>The AI teacher&rsquo;s working files:</strong> kept on the computer that runs the teacher until
            they are cleared. Deleting a request or your account doesn&rsquo;t reach them on its own; write to us and we
            will clear yours.</li>
        </ul>
      </Section>

      <Section id="delete" title="7. Deleting your data">
        <ul>
          <li><strong>A request:</strong> press <em>delete</em> on it in <Link href="/learn">Learn</Link>. If it has no
            lesson yet, it is gone. If it has one, your words, your files and the follow-up thread are deleted, and the
            lesson stays public with nothing linking it to you.</li>
          <li><strong>Your account:</strong> go to <Link href="/account">your account</Link> and choose <em>Delete my
            account</em>. Your account, your files, your follow-ups and any request without a lesson are deleted, and
            requests with a lesson are handled as above. If you sign in again later, you start with a new, empty
            account.</li>
          <li><strong>A lesson:</strong> if you want a lesson made from your request taken down, write to us at{' '}
            <Contact />.</li>
        </ul>
        <p>
          These take your data out of Popsmosis straight away. Copies in server logs, backups and the AI teacher&rsquo;s
          working files (<a href="#retention">section 6</a>) go later; write to us if you want them cleared sooner.
        </p>
      </Section>

      <Section id="rights" title="8. Your rights">
        <p>
          Depending on where you live (for example the European Economic Area, the UK or California), you have the right
          to know what we hold about you and get a copy of it, to correct it, to delete it, to object to or restrict how
          we use it, and to take it with you. You can also complain to your data protection authority.
        </p>
        <p>
          You can delete your data yourself (<a href="#delete">section 7</a>). For anything else, email <Contact /> from
          the address you sign in with. We will answer within 30 days, and we won&rsquo;t treat you differently for
          asking.
        </p>
        <p>
          Washington residents: health information you choose to include in what you send is covered by our{' '}
          <Link href="/consumer-health-data">Consumer Health Data Privacy Policy</Link>, which sets out your rights over
          it under Washington&rsquo;s My Health My Data Act.
        </p>
        <p>
          California residents: we don&rsquo;t sell or share personal information as the California Consumer Privacy Act
          defines those terms.
        </p>
      </Section>

      <Section id="children" title="9. Children">
        <p>
          You must be {MIN_AGE} or older to have an account, or older where your country requires it (see the{' '}
          <Link href="/terms#who-can-use">Terms</Link>). Anyone can watch the Droplets tab without an account, and doing
          so collects nothing about them beyond the technical logs in <a href="#collect">section 3</a>. If you think a
          child under {MIN_AGE} has an account, tell us and we will delete it.
        </p>
      </Section>

      <Section id="cookies" title="10. Cookies">
        <p>
          Popsmosis sets one cookie, <code>popsmosis_session</code>, which keeps you signed in. It lasts 30 days, renews
          while you use the site, and is removed when you log out or delete your account. While you sign in, your browser
          also holds two short-lived values for the sign-in itself: a random code that protects it, and the page to
          return to. There are no analytics, advertising or third-party tracking cookies. The videos are served by our
          storage provider, which, like any website, sees your IP address when your browser fetches them.
        </p>
      </Section>

      <Section id="security" title="11. Security">
        <p>
          Everything travels over encrypted connections. The sign-in cookie can&rsquo;t be read by scripts on the page,
          keys are stored only in scrambled (hashed) form, and only our admins can see private data. No system is
          perfectly secure. If a breach affects your data, we will tell you, and the authorities where the law requires.
        </p>
      </Section>

      <Section id="transfers" title="12. Where your data is processed">
        <p>
          Our providers run servers in the United States and other countries, so your data may be processed outside the
          country you live in. Where the law requires it, we rely on safeguards such as the European Commission&rsquo;s
          Standard Contractual Clauses in our providers&rsquo; terms.
        </p>
      </Section>

      <Section id="changes" title="13. Changes to this policy">
        <p>
          When we change this policy, we update the date at the top. If a change matters, such as making something public
          that is private today, we will say so on the site before it takes effect.
        </p>
      </Section>

      <Section id="contact" title="14. Contact">
        <p>Questions, requests or complaints about your data: <Contact />.</p>
      </Section>
    </LegalPage>
  );
}
