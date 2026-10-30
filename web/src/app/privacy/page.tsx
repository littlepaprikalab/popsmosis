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
 * a request back), DELETE /api/auth/me (deleting an account), lib/droplet.ts and lib/engagement.ts (what the Droplets
 * tab and its comments show), routes/plays.ts and web/src/lib/plays.ts (the play totals), lib/terms.ts (the record of
 * agreeing). Keep them in step: a promise the code does not keep is the thing a privacy claim is made of. Move
 * LAST_UPDATED in lib/legal.ts when this page changes, and TERMS_VERSION in api/src/lib/terms.ts when people must
 * agree again.
 */
export default function PrivacyPage() {
  return (
    <LegalPage
      title="Privacy Policy"
      summary={
        <ul>
          <li><strong>The lessons are public.</strong> Every droplet the AI teacher makes is published on the{' '}
            <Link href="/droplets">Droplets tab</Link> for anyone to watch, with no account needed.</li>
          <li><strong>Your requests are private.</strong> We don&rsquo;t publish your email address, your photo, your
            requests, your files or your follow-up questions. Comments you choose to post on a droplet are public, with
            your first name and last initial.</li>
          <li><strong>You are in control.</strong> You can delete a request, a comment, or your whole account at any
            time. A lesson made from your request stays public, with nothing linking it to you.</li>
          <li><strong>No ads, no tracking, no selling.</strong> We don&rsquo;t sell your data, show ads, use tracking
            cookies or analytics that identify you, or train AI models on what you send. We count how far each video is
            watched, in totals that say nothing about who watched.</li>
        </ul>
      }
    >
      <Section id="who" title="1. Who we are">
        <p>
          Popsmosis is a free learning site. You ask for a topic and an AI teacher makes you a <em>droplet</em>: a short
          lesson, checked by AI, with a narrated video and a song. Popsmosis is run by <strong>{OPERATOR_NAME}</strong>,
          based in {OPERATOR_LOCATION} (&ldquo;we&rdquo;, &ldquo;us&rdquo;), which is responsible for your personal data
          as this policy describes. You can reach us at <Contact />. This policy is part of our{' '}
          <Link href="/terms">Terms of Use</Link>. If you are in Washington State, our{' '}
          <Link href="/consumer-health-data">Consumer Health Data Privacy Policy</Link> also applies.
        </p>
      </Section>

      <Section id="public-private" title="2. What is public and what is private">
        <div className="legal-split">
          <div>
            <h3 style={{ color: 'var(--accent-green)' }}>Public: anyone can see it</h3>
            <ul>
              <li>The lesson: its text, diagrams, narration, song and lyrics</li>
              <li>A title naming the subject, such as &ldquo;AP Biology: ATP synthesis&rdquo;</li>
              <li>Hashtags naming the subject and its key terms, such as &ldquo;#apbio&rdquo;, so the lessons can be
                searched</li>
              <li>When the lesson was made</li>
              <li>Comments you post on a droplet, with your first name and last initial (&ldquo;Grace H.&rdquo;) and
                when you posted them</li>
              <li>How many likes and comments a droplet has</li>
            </ul>
          </div>
          <div>
            <h3 style={{ color: 'var(--accent-purple)' }}>Private: you, the AI teacher and our admins</h3>
            <ul>
              <li>Your full name, email address and Google profile photo</li>
              <li>Your requests, in your own words</li>
              <li>The files you attach</li>
              <li>Your follow-up questions and the teacher&rsquo;s answers to them</li>
              <li>Which droplets you liked</li>
            </ul>
          </div>
        </div>
        <p>
          &ldquo;Private&rdquo; means Popsmosis doesn&rsquo;t show it to other users or the public. Our service
          providers process it for us (<a href="#providers">section 5</a>), and we may disclose it where the law
          requires.
        </p>
        <p>
          A lesson is written for anyone learning the topic, not about you. It is shaped by your request, though (the
          course, the level, a song style you asked for), so keep personal details out of what you type, and never include
          anyone else&rsquo;s. Popsmosis refuses requests, follow-ups and comments that contain an email address or a
          phone number, but it can&rsquo;t catch everything. If a lesson says something that identifies you or someone
          else, tell us and we will take it down.
        </p>
        <p>
          Anything you post publicly can be seen, copied and kept by others. Deleting a comment removes it from
          Popsmosis, but not from copies other people may have made, which we don&rsquo;t control.
        </p>
      </Section>

      <Section id="collect" title="3. What we collect">
        <dl>
          <dt>When you sign in with Google</dt>
          <dd>Your name, email address, profile photo and Google account ID. We don&rsquo;t see your Google password, and
            we don&rsquo;t ask for access to anything else in your Google account.</dd>
          <dt>Your agreement to our terms</dt>
          <dd>When you agreed to the Terms of Use and this policy, and which version.</dd>
          <dt>What you send and do</dt>
          <dd>Your requests, the files you attach (such as photos of your notes), your follow-up questions, your comments
            and the droplets you like.</dd>
          <dt>What the teacher makes for you</dt>
          <dd>The lessons, videos and replies to your follow-ups.</dd>
          <dt>How the videos are watched</dt>
          <dd>How many times each droplet&rsquo;s videos are started and how far they play (a quarter, half, three
            quarters, the end), and how often a hook clip is shared or downloaded. These are totals for each video and
            nothing else. They are counted without your cookie, your account, your IP address or any identifier of your
            device or browser, and with no time, so they can&rsquo;t say who watched or when. To stop anyone inflating
            them, your IP address is held in memory for up to an hour, only in a scrambled form, and is never stored
            with them.</dd>
          <dt>Technical information</dt>
          <dd>A cookie that keeps you signed in (<a href="#cookies">section 10</a>). Our hosting provider&rsquo;s server
            logs, which record IP addresses, the addresses requested, times and errors. IP addresses are also held
            briefly in memory to stop any one visitor overloading the site.</dd>
        </dl>
        <p>
          We don&rsquo;t collect payment details, your precise location, your contacts, or advertising or analytics
          identifiers.
        </p>
      </Section>

      <Section id="use" title="4. How we use it">
        <ul>
          <li><strong>To make your lessons.</strong> The AI teacher reads your request, your files and your follow-ups to
            write the lesson, check it and answer you.</li>
          <li><strong>To run your account.</strong> Signing you in, showing you your requests, and showing your comments
            and likes.</li>
          <li><strong>To publish.</strong> Each finished lesson goes on the Droplets tab without anything about you, and
            your comments appear under the droplet you posted them on.</li>
          <li><strong>To make better lessons.</strong> The play totals show which droplets students watch to the end, so
            we can learn what works.</li>
          <li><strong>To keep Popsmosis safe and working.</strong> Limits on how much one person can ask or post,
            preventing abuse, fixing bugs, and removing lessons and comments that break our{' '}
            <Link href="/terms">Terms</Link>.</li>
          <li><strong>To meet legal obligations and protect rights.</strong> Keeping a record that you agreed to our
            terms, answering lawful requests, enforcing our terms, and establishing or defending legal claims.</li>
        </ul>
        <p>
          We don&rsquo;t sell your data, use it for advertising, or use it to train AI models. If you are in the European
          Economic Area or the UK, the legal bases are: providing the service you asked for (contract); keeping it secure,
          preventing abuse, publishing the lessons and defending legal claims (our legitimate interests); and meeting
          legal obligations.
        </p>
      </Section>

      <Section id="providers" title="5. Who helps us run it, and when we disclose data">
        <p>We use these companies to run Popsmosis. Each one gets what its job needs, under its own terms.</p>
        <dl>
          <dt>Google</dt>
          <dd>Sign-in. Google&rsquo;s sign-in page applies{' '}
            <a href="https://policies.google.com/privacy" rel="noopener noreferrer" target="_blank">Google&rsquo;s privacy policy</a>.</dd>
          <dt>Render</dt>
          <dd>Hosts the website and its server, in the United States. Everything passes through it, and it keeps the
            server logs.</dd>
          <dt>Supabase</dt>
          <dd>Our database and video storage: your account, requests, files, follow-ups, comments and likes, and the
            public videos.</dd>
          <dt>Anthropic (Claude)</dt>
          <dd>The AI teacher. It reads your requests, files and follow-ups to write, check and explain the lessons. It
            does not receive your comments.</dd>
          <dt>Meta</dt>
          <dd>Only if we switch the lesson writer to Meta&rsquo;s AI models. It would then receive the same as
            Anthropic.</dd>
          <dt>ElevenLabs, or Google&rsquo;s Lyria</dt>
          <dd>The narration and the songs. They receive the lesson script, the lyrics and the song&rsquo;s style, which can
            come from a style you asked for. Not your name or account.</dd>
          <dt>Inkmory</dt>
          <dd>A public journal where the AI teacher writes about its own work. It gets each topic&rsquo;s subject and a
            reference number for it, never your words or anything that identifies you.</dd>
        </dl>
        <p>
          While it makes a lesson, the AI teacher keeps working files, which can include your request and your files, on
          the computer that runs it, so it can retry a step that failed. They are not published (see{' '}
          <a href="#retention">section 6</a> for how long they stay).
        </p>
        <p>
          We are not responsible for the privacy practices of these providers or of other websites linked from
          Popsmosis. We may also disclose information: to comply with the law, a court order or another lawful request; to
          enforce our terms; to protect the rights, property or safety of our users, the public or us; and if Popsmosis
          passes to a new operator, who would then be bound by this policy for the data it receives.
        </p>
      </Section>

      <Section id="retention" title="6. How long we keep it">
        <ul>
          <li><strong>Your account and your record of agreeing to our terms:</strong> until you delete your
            account.</li>
          <li><strong>Your requests, follow-ups, comments and likes:</strong> until you delete them or your
            account.</li>
          <li><strong>Your files:</strong> each file&rsquo;s contents are deleted {ATTACHMENT_RETENTION_DAYS} days after
            you send it. Its name and size stay with your request, so it still shows what you sent.</li>
          <li><strong>When you asked:</strong> the time of each request and follow-up you send, but not what it said,
            for two days, so the daily and hourly limits hold. Deleting a request or a follow-up leaves it until then;
            deleting your account removes it at once.</li>
          <li><strong>Lessons and videos:</strong> published for as long as Popsmosis runs, unless they are taken down.</li>
          <li><strong>Play totals:</strong> for as long as their droplet is kept; they go with it.</li>
          <li><strong>Server logs and backups:</strong> kept by our providers for a limited time, after which they
            expire.</li>
          <li><strong>The AI teacher&rsquo;s working files:</strong> kept on the computer that runs the teacher until
            they are cleared. Deleting a request or your account doesn&rsquo;t reach them on its own; write to us and we
            will clear yours.</li>
        </ul>
        <p>
          We may keep information for longer where the law requires it, or where we need it to deal with a legal claim
          or dispute, and only for as long as that lasts.
        </p>
      </Section>

      <Section id="delete" title="7. Deleting your data">
        <ul>
          <li><strong>A request:</strong> press <em>delete</em> on it in <Link href="/learn">Learn</Link>. If it has no
            lesson yet, it is deleted. If it has one, your words, your files and the follow-up thread are deleted, and
            the lesson stays public with nothing linking it to you.</li>
          <li><strong>A comment or a like:</strong> delete the comment, or take back the like, on the droplet&rsquo;s
            page.</li>
          <li><strong>Your account:</strong> go to <Link href="/account">your account</Link> and choose <em>Delete my
            account</em>. Your account, files, follow-ups, comments, likes and any request without a lesson are deleted,
            and requests with a lesson are handled as above. If you sign in again later, you start with a new, empty
            account.</li>
          <li><strong>A lesson:</strong> if you want a lesson made from your request taken down, write to us at{' '}
            <Contact />.</li>
        </ul>
        <p>
          These take your data out of Popsmosis&rsquo;s database at once. Copies in server logs, backups and the AI
          teacher&rsquo;s working files (<a href="#retention">section 6</a>) go later; write to us if you want them
          cleared sooner.
        </p>
      </Section>

      <Section id="rights" title="8. Your rights">
        <p>
          Depending on where you live (for example the European Economic Area, the UK, California or Washington), you may
          have the right to know what we hold about you and get a copy of it, to correct it, to delete it, to object to
          or restrict how we use it, and to take it with you. You can also complain to your data protection authority.
        </p>
        <p>
          You can delete your data yourself (<a href="#delete">section 7</a>). For anything else, email <Contact /> from
          the address you sign in with, so we can confirm the request is yours; we may ask for more to verify it. We aim to
          answer within 30 days, and always within the time the law where you live allows. We won&rsquo;t treat you
          differently for asking.
        </p>
        <p>
          Washington residents: health information you choose to include in what you send is covered by our{' '}
          <Link href="/consumer-health-data">Consumer Health Data Privacy Policy</Link>, which sets out your rights over
          it under Washington&rsquo;s My Health My Data Act.
        </p>
        <p>
          California residents: we don&rsquo;t sell or share personal information as the California Consumer Privacy Act
          defines those terms, and we don&rsquo;t use sensitive personal information to infer things about you.
        </p>
      </Section>

      <Section id="children" title="9. Children">
        <p>
          You must be {MIN_AGE} or older to have an account, or older where your country requires it (see the{' '}
          <Link href="/terms#who-can-use">Terms</Link>). We don&rsquo;t knowingly collect personal information from
          children under {MIN_AGE}. Anyone can watch the Droplets tab without an account, and doing so collects nothing
          about them beyond the technical logs in <a href="#collect">section 3</a>; the play totals there are not about
          anyone. If you think a child under{' '}
          {MIN_AGE} has an account, tell us and we will delete it.
        </p>
      </Section>

      <Section id="cookies" title="10. Cookies and tracking">
        <p>
          Popsmosis sets one cookie, <code>popsmosis_session</code>, which keeps you signed in. It lasts 30 days, renews
          while you use the site, and is removed when you log out or delete your account. While you sign in, your browser
          also holds a few short-lived values for the sign-in itself: a random code that protects it, the page to return
          to, and that you ticked the box agreeing to our terms. There are no analytics, advertising or third-party
          tracking cookies. The videos are served by our storage provider, which, like any website, sees your IP address
          when your browser fetches them.
        </p>
        <p>
          We don&rsquo;t track you across other websites, and we don&rsquo;t let third parties collect information about
          your activity on Popsmosis over time or across sites. So Popsmosis works the same whether or not your browser
          sends a &ldquo;Do Not Track&rdquo; or Global Privacy Control signal: there is no tracking for it to turn
          off.
        </p>
      </Section>

      <Section id="security" title="11. Security">
        <p>
          We use reasonable safeguards for the data we hold. For example, it travels over encrypted connections, the
          sign-in cookie can&rsquo;t be read by scripts on the page, keys are stored only in scrambled (hashed) form, and
          only our admins can see private data in Popsmosis. No system is completely secure, and we can&rsquo;t guarantee
          the security of your data. If a breach affects your data, we will notify you and the authorities as the law
          requires.
        </p>
      </Section>

      <Section id="transfers" title="12. Where your data is processed">
        <p>
          We are based in the United States, and our providers run servers in the United States and other countries, so
          your data may be processed outside the country you live in, where data protection laws may differ. Where the
          law requires it, we rely on safeguards such as the European Commission&rsquo;s Standard Contractual Clauses in
          our providers&rsquo; terms.
        </p>
      </Section>

      <Section id="changes" title="13. Changes to this policy">
        <p>
          When we change this policy, we update the date at the top. If a change matters, such as making something public
          that is private today, we will tell you on the site before it takes effect and ask you to agree again before you
          go on using Popsmosis.
        </p>
      </Section>

      <Section id="contact" title="14. Contact">
        <p>
          Questions, requests or complaints about your data: <Contact />, {OPERATOR_NAME}, {OPERATOR_LOCATION}.
        </p>
      </Section>
    </LegalPage>
  );
}
