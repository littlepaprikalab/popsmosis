import type { Metadata } from 'next';
import Link from 'next/link';
import { LegalPage, Section, Contact } from '@/components/LegalPage';
import { OPERATOR_NAME, MIN_AGE, GOVERNING_LAW, COURTS, CONTENT_LICENSE } from '@/lib/legal';

export const metadata: Metadata = {
  title: 'Terms of Use',
  description: 'The rules for using Popsmosis: lessons are public and made by AI, your words stay private, and how the lessons may be shared.',
};

/** The Terms of Use. Move LAST_UPDATED in lib/legal.ts when this page changes. */
export default function TermsPage() {
  const law = GOVERNING_LAW || `the place where ${OPERATOR_NAME} is based`;
  return (
    <LegalPage
      title="Terms of Use"
      summary={
        <ul>
          <li><strong>Popsmosis is free.</strong> Ask for a topic and an AI teacher makes you a short lesson.</li>
          <li><strong>Every lesson is published for everyone</strong> on the Droplets tab. Your name and your words
            stay private.</li>
          <li><strong>Lessons are made by AI.</strong> They are fact-checked by AI too, but they can still be wrong.
            Check anything important.</li>
          <li><strong>Share them freely</strong> for non-commercial use, with credit ({CONTENT_LICENSE.name}).</li>
          <li><strong>Be decent.</strong> No harmful, illegal or abusive requests. You must be {MIN_AGE} or older to
            sign in.</li>
        </ul>
      }
    >
      <Section id="agreement" title="1. Agreeing to these terms">
        <p>
          Popsmosis is run by <strong>{OPERATOR_NAME}</strong> (&ldquo;we&rdquo;, &ldquo;us&rdquo;). By using it, whether
          you sign in or only watch, you agree to these terms, and you confirm you have read the{' '}
          <Link href="/privacy">Privacy Policy</Link>, which explains what we do with your data. If you don&rsquo;t
          agree, please don&rsquo;t use Popsmosis.
        </p>
      </Section>

      <Section id="who-can-use" title="2. Who can use Popsmosis">
        <ul>
          <li>Anyone can watch the <Link href="/droplets">Droplets tab</Link> without an account.</li>
          <li>To sign in, you must be at least {MIN_AGE}. If your country sets a higher age for using online services
            without a parent&rsquo;s permission (up to 16 in parts of Europe), you must be that age, or have your parent
            or guardian&rsquo;s permission.</li>
          <li>If you are under 18, read these terms with a parent or guardian.</li>
          <li>Teachers and schools are welcome to show droplets in class, which needs no accounts. Please don&rsquo;t
            create accounts for students under {MIN_AGE}.</li>
        </ul>
      </Section>

      <Section id="account" title="3. Your account">
        <p>
          You sign in with your Google account, one person per account. Keep your Google account secure: you are
          responsible for what is done through your Popsmosis account. You can delete it at any time from{' '}
          <Link href="/account">your account page</Link>.
        </p>
      </Section>

      <Section id="how-it-works" title="4. How Popsmosis works">
        <p>
          You ask for a topic. An AI teacher writes a short lesson, checks each line with a second AI pass, and makes a
          narrated video and a song from it. You can ask follow-up questions under your lesson. The teacher works in
          passes, so a lesson can take a while, and there are limits on how much one person can ask in a day.
        </p>
        <p>
          We may decline a topic, stop making a lesson, or change how Popsmosis works. It is a free service and we
          can&rsquo;t promise it will always be available.
        </p>
      </Section>

      <Section id="public-lessons" title="5. Lessons are public">
        <ul>
          <li>When the teacher makes a lesson from your request, it is published on the Droplets tab for anyone to watch,
            share and learn from. That is the point of Popsmosis: a lesson made for one learner helps everyone who comes
            after them.</li>
          <li>What is published is the lesson (its text, diagrams, videos, song and lyrics) and a title naming the
            subject. Never your name, photo, email address, your words, your files or your follow-up questions.</li>
          <li>A lesson made from your request is not yours alone. Other people may ask for the same topic, and the same
            lesson may be shown to them.</li>
          <li>You can delete your request at any time. The lesson stays public, with nothing linking it to you. If a
            lesson identifies you or anyone else, tell us at <Contact /> and we will take it down.</li>
        </ul>
      </Section>

      <Section id="your-content" title="6. What you send us">
        <ul>
          <li>You keep ownership of what you send: your requests, files and follow-up questions.</li>
          <li>You allow us to store it, show it to you, and pass it to the AI teacher and the providers listed in the{' '}
            <Link href="/privacy#providers">Privacy Policy</Link>, only to make your lessons, answer your questions and
            run Popsmosis. This permission ends when you delete it, except for lessons that were already made.</li>
          <li>Only send what you have the right to share, such as your own notes, or short extracts of course material
            for your own study.</li>
          <li>Keep personal details out: no addresses, phone numbers, health information, ID numbers or passwords, and
            nothing at all about other people.</li>
        </ul>
      </Section>

      <Section id="license" title="7. Sharing the lessons">
        <p>
          The droplets are free to watch. To the extent we hold rights in them, the lessons, videos, songs and lyrics are
          licensed under <a href={CONTENT_LICENSE.url} rel="noopener noreferrer" target="_blank">{CONTENT_LICENSE.title}</a>{' '}
          ({CONTENT_LICENSE.name}). That means you may copy, share and adapt them if you:
        </p>
        <ul>
          <li>credit Popsmosis, with a link to the droplet;</li>
          <li>don&rsquo;t use them commercially (showing them in a class, a study group or a free website is fine);
            and</li>
          <li>share anything you make from them under the same license.</li>
        </ul>
        <p>
          The Popsmosis name, logo and website design are not covered by this license. Material made by AI may not be
          protected by copyright in some countries, and nothing here claims rights we don&rsquo;t have. Some songs carry
          an invisible watermark that marks them as made by AI. Please don&rsquo;t remove it.
        </p>
      </Section>

      <Section id="ai" title="8. Made by AI: check what matters">
        <ul>
          <li>Everything in a droplet is made by AI: the lesson, the diagrams, the narrating voice and the song.</li>
          <li>Each line is fact-checked by AI before it is posted, but AI makes mistakes, and the check can miss them.
            A lesson may be wrong, incomplete or out of date.</li>
          <li>Popsmosis is for learning and revision. It is not medical, legal, financial, safety or any other
            professional advice, and it doesn&rsquo;t replace your teacher, your textbook or your course&rsquo;s
            official materials. Check anything important with a reliable source.</li>
          <li>Found a mistake? Tell us at <Contact />.</li>
        </ul>
      </Section>

      <Section id="acceptable-use" title="9. Acceptable use">
        <p>Don&rsquo;t use Popsmosis to:</p>
        <ul>
          <li>ask for anything illegal, or for help harming people, such as weapons, dangerous drugs, self-harm or
            breaking into computers;</li>
          <li>harass, threaten or demean anyone, or ask for hateful content;</li>
          <li>ask for sexual content, and never anything that sexualizes children;</li>
          <li>ask for lessons about private individuals, or include anyone&rsquo;s personal information;</li>
          <li>infringe someone&rsquo;s copyright or other rights;</li>
          <li>break, overload or scrape Popsmosis, get around its limits or security, or get into other people&rsquo;s
            accounts or data;</li>
          <li>pretend to be someone else, or suggest Popsmosis endorses you.</li>
        </ul>
        <p>
          We may refuse requests, take down lessons and suspend or remove accounts that break these rules. If you or
          someone else is in danger, contact your local emergency services: Popsmosis can&rsquo;t help in an emergency.
        </p>
      </Section>

      <Section id="report" title="10. Reporting a problem">
        <p>
          Write to <Contact /> to report a lesson that is wrong, harmful, identifies someone, or uses your work without
          permission. For a copyright complaint, please include your contact details, the work you own, the link to the
          droplet, a statement that you believe in good faith the use is not authorized, and a statement that your notice
          is accurate and that you own the work or may act for its owner, signed with your full name. We take down
          infringing material and may remove the accounts of people who infringe repeatedly.
        </p>
      </Section>

      <Section id="no-warranty" title="11. The service is provided as is">
        <p>
          Popsmosis is free and is provided &ldquo;as is&rdquo; and &ldquo;as available&rdquo;. To the fullest extent the
          law allows, we make no promises about it, including that it will be accurate, always available, free of errors,
          or suitable for any particular purpose. We may change, pause or end any part of it, including removing
          lessons, at any time.
        </p>
      </Section>

      <Section id="liability" title="12. Limits on our liability">
        <p>
          To the fullest extent the law allows, we are not liable for any indirect, incidental, special, consequential or
          punitive loss, or for lost data, profits or opportunities, arising from your use of Popsmosis or its lessons.
          Our total liability for any claim about Popsmosis is limited to US$50. Nothing in these terms limits liability
          that the law does not allow to be limited, such as for death or personal injury caused by negligence, or for
          fraud.
        </p>
      </Section>

      <Section id="ending" title="13. Ending">
        <p>
          You can stop using Popsmosis and delete your account whenever you like. We may suspend or end your access if
          you break these terms, or to protect Popsmosis or other people. Sections 5, 7, 11, 12 and 15 continue to apply
          after your account ends.
        </p>
      </Section>

      <Section id="changes" title="14. Changes to these terms">
        <p>
          We may update these terms. We will change the date at the top, and for significant changes give notice on the
          site before they take effect. If you keep using Popsmosis after that, the new terms apply. If you don&rsquo;t
          agree with them, stop using Popsmosis and delete your account.
        </p>
      </Section>

      <Section id="law" title="15. Law and disputes">
        <p>
          These terms are governed by the laws of {law}, without regard to its rules on conflicts of law. Disputes about
          Popsmosis are heard in {COURTS}. If you use Popsmosis as a consumer, you keep any protections the law where you
          live gives you, including the right to bring a claim in your local courts. Please write to us first: most
          problems can be solved by email. If part of these terms can&rsquo;t be enforced, the rest still applies. These
          terms and the Privacy Policy are the whole agreement between you and us about Popsmosis.
        </p>
      </Section>

      <Section id="contact" title="16. Contact">
        <p>Questions about these terms: <Contact />.</p>
      </Section>
    </LegalPage>
  );
}
