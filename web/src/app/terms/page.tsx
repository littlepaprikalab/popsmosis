import type { Metadata } from 'next';
import Link from 'next/link';
import { LegalPage, Section, Contact } from '@/components/LegalPage';
import { OPERATOR_NAME, OPERATOR_LOCATION, MIN_AGE, GOVERNING_LAW, COURTS, CONTENT_LICENSE } from '@/lib/legal';

export const metadata: Metadata = {
  title: 'Terms of Use',
  description: 'The rules for using Popsmosis: lessons are public and made by AI, your requests stay private, how lessons may be shared, and how disputes are settled.',
};

/**
 * The Terms of Use. People agree to them by ticking the box on the sign-in page (components/AgreeToTerms.tsx), and the
 * API records the version and the time (api/src/lib/terms.ts). For a change people must agree to again, move
 * TERMS_VERSION there; for any change, move LAST_UPDATED in lib/legal.ts. AgreeToTerms names section 17 (#disputes).
 *
 * Section 17 is written to stay enforceable: both sides bound alike, fees as the AAA's consumer rules set them, no
 * shortened time limits, hearings near the person, a small claims choice and a way to opt out. Washington courts have
 * struck down arbitration clauses that were one-sided, so keep it that way.
 */
export default function TermsPage() {
  const law = GOVERNING_LAW || `the place where ${OPERATOR_NAME} is based`;
  return (
    <LegalPage
      title="Terms of Use"
      summary={
        <ul>
          <li><strong>Popsmosis is free.</strong> Ask for a topic and an AI teacher makes you a short lesson.</li>
          <li><strong>Every lesson is published for everyone</strong> on the Droplets tab. Your requests and follow-ups
            stay private. Comments you post are public.</li>
          <li><strong>Lessons are made by AI and can be wrong.</strong> They are for learning, not professional advice.
            Check anything important, and don&rsquo;t try anything risky a lesson describes.</li>
          <li><strong>Share lessons freely</strong> for non-commercial use, with credit ({CONTENT_LICENSE.name}).</li>
          <li><strong>Be decent.</strong> No harmful, illegal or abusive requests or comments. You must be {MIN_AGE} or
            older to sign in.</li>
          <li><strong>Disputes go to individual arbitration, not class actions or jury trials</strong>, unless you opt
            out within 30 days (<a href="#disputes">section 17</a>). Please read it.</li>
        </ul>
      }
    >
      <Section id="agreement" title="1. Agreeing to these terms">
        <p>
          Popsmosis is run by <strong>{OPERATOR_NAME}</strong>, based in {OPERATOR_LOCATION} (&ldquo;we&rdquo;,
          &ldquo;us&rdquo;). These terms are a contract between you and us. You agree to them when you tick the box to
          sign in, and by using Popsmosis in any way, including only watching. The{' '}
          <Link href="/privacy">Privacy Policy</Link> and the{' '}
          <Link href="/consumer-health-data">Consumer Health Data Privacy Policy</Link> explain what we do with your data
          and are part of these terms. If you don&rsquo;t agree, don&rsquo;t use Popsmosis.
        </p>
      </Section>

      <Section id="who-can-use" title="2. Who can use Popsmosis">
        <ul>
          <li>Anyone can watch the <Link href="/droplets">Droplets tab</Link> without an account.</li>
          <li>To sign in, you must be at least {MIN_AGE}. If your country sets a higher age for using online services
            without a parent&rsquo;s permission (up to 16 in parts of Europe), you must be that age, or have your parent
            or guardian&rsquo;s permission.</li>
          <li>If you are under 18, you may use Popsmosis only with the involvement of a parent or guardian, who should
            read these terms with you.</li>
          <li><strong>Parents and guardians:</strong> if you let a minor use Popsmosis, you agree to these terms on their
            behalf and are responsible for how they use it, including anything they post.</li>
          <li>Teachers and schools are welcome to show droplets in class, which needs no accounts. Please don&rsquo;t
            create accounts for students under {MIN_AGE}.</li>
          <li>You may not use Popsmosis if the law forbids you from doing so, or if we have removed your account
            before.</li>
        </ul>
      </Section>

      <Section id="account" title="3. Your account">
        <p>
          You sign in with your Google account, one person per account. Keep your Google account secure: you are
          responsible for everything done through your Popsmosis account, and you should tell us at <Contact /> if you
          think someone else has used it. You can delete your account at any time from{' '}
          <Link href="/account">your account page</Link>.
        </p>
      </Section>

      <Section id="how-it-works" title="4. How Popsmosis works">
        <p>
          You ask for a topic. An AI teacher writes a short lesson, checks each line with a second AI pass, and makes a
          narrated video and a song from it. You can ask follow-up questions under your lesson. The teacher works in
          passes, so a lesson can take a while, and there are limits on how much one person can ask.
        </p>
        <p>
          Popsmosis is free, and we offer it as it is. We may decline a topic, stop making a lesson, change or remove any
          feature or lesson, limit use, or pause or end Popsmosis at any time, without notice and without liability to
          you.
        </p>
      </Section>

      <Section id="public-lessons" title="5. Lessons are public">
        <ul>
          <li>When the teacher makes a lesson from your request, it is published on the Droplets tab for anyone to
            watch, share and learn from. That is the point of Popsmosis: a lesson made for one learner helps everyone who
            comes after them.</li>
          <li>What is published is the lesson (its text, diagrams, videos, song and lyrics) and a title naming the
            subject. Never your name, photo, email address, your request, your files or your follow-up questions.</li>
          <li>A lesson made from your request is not yours. Other people may ask for the same topic and be shown the
            same lesson. To the extent you have any rights in a lesson because of what you asked for, you give us a
            permanent, irrevocable, worldwide, royalty-free license to use, publish and adapt it, and to let others use
            it as <a href="#license">section 8</a> allows.</li>
          <li>You can delete your request at any time. The lesson stays public, with nothing linking it to you. If a
            lesson identifies you or anyone else, tell us at <Contact /> and we will take it down.</li>
        </ul>
      </Section>

      <Section id="your-content" title="6. What you send us">
        <ul>
          <li>You keep ownership of what you send: your requests, files, follow-up questions and comments.</li>
          <li>For requests, files and follow-ups, you give us a non-exclusive, worldwide, royalty-free license to store,
            copy, process and show them to you, and to pass them to the AI teacher and the providers listed in the{' '}
            <Link href="/privacy#providers">Privacy Policy</Link>, to make your lessons, answer you and run Popsmosis.
            This license ends when you delete them, except for lessons already made and copies in backups until they
            expire.</li>
          <li>You promise that you have the right to send what you send, and that it doesn&rsquo;t break the law or
            anyone&rsquo;s rights. Your own notes, or short extracts of course material for your own study, are
            fine.</li>
          <li>Keep personal details out: no addresses, phone numbers, health information, ID numbers or passwords, and
            nothing about other people.</li>
          <li>We don&rsquo;t review what you send before the AI teacher reads it, and we are not responsible for
            it.</li>
        </ul>
      </Section>

      <Section id="comments" title="7. Comments and likes">
        <ul>
          <li>Signed-in people can like a droplet and comment on it. <strong>Comments are public:</strong> anyone can read
            them, with your first name and last initial. Which droplets you like is private; only the count is
            shown.</li>
          <li>You are responsible for your comments. You give us a non-exclusive, worldwide, royalty-free license to
            host, show, copy and distribute them on Popsmosis for as long as they are posted, and in backups until they
            expire.</li>
          <li>Comments are the views of the people who post them, not ours. We don&rsquo;t check them before they appear
            and we don&rsquo;t have to, but we may remove any comment, at any time, for any reason.</li>
          <li>Anything you post publicly can be seen, copied and kept by others. Deleting a comment removes it from
            Popsmosis, but not from copies others may have made.</li>
        </ul>
      </Section>

      <Section id="license" title="8. Sharing the lessons">
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
          The license does not cover comments, the Popsmosis name and logo, or the website&rsquo;s design and code. Some
          material made by AI may not be protected by copyright, and nothing here claims rights we don&rsquo;t have. Some
          songs carry an invisible watermark that marks them as made by AI. Don&rsquo;t remove it.
        </p>
      </Section>

      <Section id="ai" title="9. Made by AI: no professional advice, no reliance">
        <ul>
          <li>Everything in a droplet is made by AI: the lesson, the diagrams, the narrating voice and the song. So are
            the teacher&rsquo;s answers to follow-up questions.</li>
          <li>Each line is checked by AI before it is posted, but AI makes mistakes, and the check can miss them. A
            lesson may be wrong, incomplete, out of date, or similar to material found elsewhere.</li>
          <li><strong>Popsmosis is for general learning and revision only.</strong> It is not medical, health, legal,
            financial, safety or any other professional advice, and it doesn&rsquo;t replace your teacher, your textbook,
            your course&rsquo;s official materials or a qualified professional. Don&rsquo;t rely on a lesson for a
            decision that matters, such as about your health, money, safety, or an exam or assignment, without checking
            it with a reliable source.</li>
          <li>You use the lessons at your own risk and are responsible for what you do with them. Found a mistake? Tell
            us at <Contact />.</li>
        </ul>
      </Section>

      <Section id="safety" title="10. Your safety">
        <ul>
          <li>A lesson may describe experiments or activities, such as in chemistry, biology, physics, cooking or sport.
            Don&rsquo;t try anything that could hurt you, someone else or property without proper supervision, training
            and safety equipment. We are not responsible for what happens if you do.</li>
          <li>Popsmosis can&rsquo;t help in an emergency. If you or someone else may be in danger, call your local
            emergency number (911 in the US). In the US, you can call or text 988 to reach the Suicide and Crisis
            Lifeline.</li>
          <li>Lessons and comments may link to other websites. We don&rsquo;t control them and are not responsible for
            them.</li>
        </ul>
      </Section>

      <Section id="acceptable-use" title="11. Acceptable use">
        <p>Don&rsquo;t use Popsmosis to:</p>
        <ul>
          <li>ask for or post anything illegal, or anything meant to help someone cause harm, such as weapons, dangerous
            drugs, self-harm or breaking into computers;</li>
          <li>harass, bully, threaten, demean or discriminate against anyone, or post hateful content;</li>
          <li>ask for or post sexual content, and never anything that sexualizes children;</li>
          <li>post personal information about anyone, or ask for lessons about private individuals;</li>
          <li>post spam, advertising or links to malware;</li>
          <li>infringe anyone&rsquo;s copyright, trademark, privacy or other rights;</li>
          <li>break, overload or scrape Popsmosis, get around its limits or security, use bots to post, or get into
            other people&rsquo;s accounts or data;</li>
          <li>pretend to be someone else, or suggest Popsmosis endorses you.</li>
        </ul>
        <p>
          We may refuse requests, remove lessons and comments, and suspend or remove accounts that break these rules, or
          that we reasonably believe put Popsmosis or others at risk.
        </p>
      </Section>

      <Section id="report" title="12. Reporting a problem, and copyright">
        <p>
          Write to <Contact /> to report a lesson or comment that is wrong, harmful, identifies someone, or uses your
          work without permission.
        </p>
        <p>
          <strong>Copyright notices.</strong> We respond to notices under the US Digital Millennium Copyright Act. Our
          agent for them is {OPERATOR_NAME}, at <Contact />. A notice must include: your physical or electronic
          signature; the work you say is infringed; the material you say infringes it and where it is on Popsmosis (its
          link); your name, address, phone number and email; a statement that you believe in good faith the use is not
          authorized by the owner, its agent or the law; and a statement, under penalty of perjury, that your notice is
          accurate and that you own the work or may act for its owner.
        </p>
        <p>
          <strong>Counter-notices.</strong> If we removed something you posted and you believe it was a mistake, you may
          send a counter-notice with: your signature; what was removed and where it was; a statement under penalty of
          perjury that you believe in good faith it was removed by mistake or misidentification; your name, address and
          phone number; and your consent to the jurisdiction of the federal court for your address (or, outside the US,
          any place we may be found) and to accept service from the person who sent the notice. We may then restore the
          material as the law allows. We remove the accounts of people who infringe repeatedly.
        </p>
      </Section>

      <Section id="no-warranty" title="13. No warranties">
        <p className="uppercase" style={{ color: 'var(--text-primary)' }}>
          Popsmosis, the lessons, the teacher&rsquo;s answers, comments and everything else on it are provided &ldquo;as
          is&rdquo; and &ldquo;as available&rdquo;, without warranties of any kind. To the fullest extent the law
          allows, we disclaim all warranties, express or implied, including warranties of accuracy, completeness,
          reliability, merchantability, fitness for a particular purpose, title and non-infringement, and any warranty
          that Popsmosis will be available, uninterrupted, secure or free of errors or harmful components.
        </p>
      </Section>

      <Section id="liability" title="14. Limits on our liability">
        <p className="uppercase" style={{ color: 'var(--text-primary)' }}>
          To the fullest extent the law allows: (a) {OPERATOR_NAME}, its owners, and the people who work on Popsmosis
          are not liable for any indirect, incidental, special, consequential, exemplary or punitive damages, or for any
          loss of data, profits, revenue, goodwill, opportunity or grades, arising out of or relating to Popsmosis, the
          lessons, comments or these terms, even if we were told such damages were possible; and (b) our total liability
          for all claims arising out of or relating to Popsmosis or these terms, under any theory, is limited to the
          greater of US$100 or what you paid us in the 12 months before the claim (Popsmosis is free, so usually
          nothing).
        </p>
        <p>
          Some places don&rsquo;t allow these exclusions or limits, so some may not apply to you. Nothing in these terms
          limits liability that the law does not allow to be limited, such as for fraud, gross negligence, willful
          misconduct, or death or personal injury caused by negligence.
        </p>
      </Section>

      <Section id="indemnity" title="15. Your responsibility for claims">
        <p>
          To the extent the law allows, you agree to defend and indemnify {OPERATOR_NAME} and its owners against any
          claim by someone else, and the losses and reasonable costs (including lawyers&rsquo; fees) that come from it,
          arising out of: what you post or send on Popsmosis; your breaking these terms; or your breaking the law or
          someone else&rsquo;s rights. We will tell you about such a claim, and may take part in or control its defense.
        </p>
      </Section>

      <Section id="ending" title="16. Suspension and ending">
        <p>
          You can stop using Popsmosis and delete your account whenever you like. We may suspend or end your access, or
          remove anything you posted, at any time if you break these terms, if the law requires it, or to protect
          Popsmosis or other people, with or without notice. Sections 5 to 9, 13 to 15, 17, 18 and 20 continue to apply
          after your account ends.
        </p>
      </Section>

      <Section id="disputes" title="17. Resolving disputes: arbitration and class action waiver">
        <p style={{ color: 'var(--text-primary)' }}>
          <strong>Please read this section carefully. It affects your rights, including your right to go to court and to
          take part in a class action.</strong>
        </p>
        <ol>
          <li><strong>Talk to us first.</strong> Before either of us starts arbitration or a lawsuit, the one with the
            claim sends the other a written description of it and what they want (to us at <Contact />; to you at the
            email on your account). We both try in good faith to settle it within 60 days. Time limits for bringing the
            claim pause during those 60 days.</li>
          <li><strong>Binding individual arbitration.</strong> If it isn&rsquo;t settled, any dispute, claim or
            controversy between you and us arising out of or relating to Popsmosis or these terms, including whether
            this section applies or can be enforced, will be decided by binding arbitration before one arbitrator, not in
            court. The American Arbitration Association (AAA) runs it under its Consumer Arbitration Rules
            (<a href="https://www.adr.org" rel="noopener noreferrer" target="_blank">adr.org</a>), and its rules decide
            the fees: we pay the share they assign to a business, and we won&rsquo;t ask you to pay our lawyers&rsquo;
            fees or costs unless the arbitrator finds your claim frivolous or brought for an improper purpose. The
            hearing is held where you live or online, by phone or on written submissions, as you prefer and the rules
            allow. The arbitrator can award the same individual relief a court could, and must follow these terms. The
            Federal Arbitration Act governs this section.</li>
          <li><strong>No class actions and no jury.</strong> You and we may bring claims against each other only
            individually, not as a plaintiff or member of a class, collective or representative proceeding, and an
            arbitrator may not combine more than one person&rsquo;s claims without everyone&rsquo;s agreement. You and we
            both give up the right to a jury trial.</li>
          <li><strong>Exceptions.</strong> Either of us may bring an individual claim in small claims court if it
            qualifies. Either of us may ask a court to stop the infringement or misuse of copyrights, trademarks or other
            intellectual property, or to stop abuse of Popsmosis&rsquo;s security.</li>
          <li><strong>Many claims at once.</strong> If 25 or more similar demands are filed against us by the same or
            coordinated lawyers, the AAA&rsquo;s rules for mass filings apply, and the demands are decided in batches,
            with each side able to ask the arbitrator to resolve common questions first.</li>
          <li><strong>Opting out.</strong> You can opt out of this section within 30 days of first agreeing to these
            terms, by emailing <Contact /> from your account&rsquo;s address with the words &ldquo;arbitration
            opt-out&rdquo;. Opting out doesn&rsquo;t affect the rest of these terms, or any earlier agreement to
            arbitrate.</li>
          <li><strong>If part of this section fails.</strong> If the ban on class or representative proceedings is found
            unenforceable for a claim, that claim (and only that claim) goes to court, after any individual arbitration
            is decided, and the rest of this section still applies. If any other part is found unenforceable, the rest
            still applies.</li>
          <li><strong>Changes.</strong> If we change this section, the change does not apply to a claim that arose
            before it. You can reject a change by emailing us within 30 days of it, and this section as you last agreed
            to it then continues to apply to you.</li>
          <li><strong>Where it does not apply.</strong> If you live in a country whose consumer law does not allow you to
            give up the right to go to court (for example in the European Union or the UK), this section does not apply
            to you, and you may bring a claim in your local courts.</li>
        </ol>
      </Section>

      <Section id="law" title="18. Law and courts">
        <p>
          These terms, and any dispute about Popsmosis, are governed by the laws of {law}, and by US federal law, without
          regard to rules on conflicts of law. Any claim that is not arbitrated (or that you opted out of arbitrating) is
          heard only in {COURTS}, and you and we agree to their jurisdiction, except that if you use Popsmosis as a
          consumer you keep any right your local law gives you to bring a claim in your local courts, and any protection
          of your local law that cannot be given up by agreement.
        </p>
      </Section>

      <Section id="changes" title="19. Changes to these terms">
        <p>
          We may update these terms. We will change the date at the top. If a change matters, we will tell you on the
          site before it takes effect and ask you to agree to the new terms before you go on using Popsmosis; if you
          don&rsquo;t agree, you can stop using Popsmosis and delete your account. Changes apply from the day they take
          effect, not to anything before it.
        </p>
      </Section>

      <Section id="general" title="20. General">
        <ul>
          <li>These terms and the two privacy policies are the whole agreement between you and us about Popsmosis, and
            replace any earlier ones.</li>
          <li>If any part of these terms can&rsquo;t be enforced, it is changed as little as needed to make it
            enforceable, and the rest still applies.</li>
          <li>If we don&rsquo;t enforce a part of these terms, we have not given up the right to enforce it later.</li>
          <li>You may not transfer these terms or your account. We may transfer them, for example if Popsmosis passes to
            a new operator.</li>
          <li>We are not responsible for delays or failures caused by events beyond our reasonable control, such as
            outages of our providers, network failures, disasters or acts of government.</li>
          <li>You agree that we may give you notices electronically, on the site or by email to your account&rsquo;s
            address, and that they satisfy any requirement that notices be in writing.</li>
          <li>If you send us ideas or feedback, we may use them freely, without paying you.</li>
          <li>Nothing in these terms makes you and us partners, employees or agents of each other, or gives anyone else
            rights under them. Headings are for convenience only.</li>
        </ul>
      </Section>

      <Section id="contact" title="21. Contact">
        <p>Questions about these terms: <Contact />, {OPERATOR_NAME}, {OPERATOR_LOCATION}.</p>
      </Section>
    </LegalPage>
  );
}
