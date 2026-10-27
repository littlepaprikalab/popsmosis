import type { Metadata } from 'next';
import Link from 'next/link';
import { LegalPage, Section, Contact } from '@/components/LegalPage';
import { OPERATOR_NAME } from '@/lib/legal';

export const metadata: Metadata = {
  title: 'Consumer Health Data Privacy Policy',
  description: 'How Popsmosis handles consumer health data under Washington\'s My Health My Data Act, and your rights over it.',
};

const AG_COMPLAINT_URL = 'https://www.atg.wa.gov/file-complaint';

/**
 * The consumer health data privacy policy Washington's My Health My Data Act (RCW 19.373.020) requires, with its own
 * link on the home page (components/Footer.tsx). The Attorney General reads the Act to mean this page holds only what
 * the Act asks for, so its sections follow RCW 19.373.020(1)(a)(i)-(v) and nothing else. Keep it in step with the
 * Privacy Policy's provider list and what lib/privacy.ts in the API deletes.
 */
export default function ConsumerHealthDataPage() {
  return (
    <LegalPage title="Consumer Health Data Privacy Policy" plain>
      <p>
        This policy is required by Washington&rsquo;s My Health My Data Act (RCW 19.373). It covers consumer health data
        that {OPERATOR_NAME}, which runs Popsmosis, collects about people in Washington State: personal information
        linked or reasonably linkable to you that identifies your past, present or future physical or mental health.
      </p>

      <Section id="collect" title="1. The consumer health data we collect, and how we use it">
        <p>
          Popsmosis does not ask for health information. It collects consumer health data only if you choose to include
          it in what you send: a request for a lesson, a file you attach, a follow-up question, or a comment you post on
          a droplet. For example: &ldquo;Explain how insulin works, I was just diagnosed with diabetes.&rdquo;
        </p>
        <p>We use it only to provide the lessons you ask for:</p>
        <ul>
          <li>the AI teacher reads it to write your lesson and answer your follow-up questions;</li>
          <li>we store it with your request so you can see it on your Learn page;</li>
          <li>a comment is published under the droplet you posted it on, because that is what posting a comment
            does;</li>
          <li>our admins can see it only to fix problems and keep the service safe.</li>
        </ul>
        <p>
          We don&rsquo;t use it for advertising, we don&rsquo;t sell it, and we don&rsquo;t infer health information from
          anything else you do on Popsmosis.
        </p>
      </Section>

      <Section id="sources" title="2. Where it comes from">
        <p>Only from you, in what you type and attach. We collect it from no other source.</p>
      </Section>

      <Section id="shared" title="3. The consumer health data we share">
        <p>
          None. We don&rsquo;t share or sell consumer health data. We disclose it only to processors that handle it on our
          behalf to provide Popsmosis: our hosting provider (Render), our database provider (Supabase) and our AI
          provider (Anthropic, or Meta if we switch the lesson writer to Meta&rsquo;s models). Under the Act, disclosure to
          a processor is not sharing.
        </p>
        <p>
          The lessons published on the Droplets tab are written for anyone learning the topic and contain nothing that
          links them to you. If you put health information in a comment, it is published with your first name and last
          initial, because you asked us to post it; you can delete the comment at any time. The AI teacher does not
          receive comments.
        </p>
      </Section>

      <Section id="third-parties" title="4. Third parties and affiliates we share it with">
        <p>None. We have no affiliates, and we share consumer health data with no third parties.</p>
      </Section>

      <Section id="rights" title="5. Your rights, and how to use them">
        <ul>
          <li><strong>Know and access.</strong> You can confirm whether we collect, share or sell your consumer health
            data, and get a copy of it, with a list of every third party and affiliate we shared it with (there are
            none).</li>
          <li><strong>Delete.</strong> You can delete a request on your <Link href="/learn">Learn</Link> page, a comment
            on its droplet&rsquo;s page, or your whole account on your <Link href="/account">account page</Link>, at any
            time; each takes it out of Popsmosis at once. You can also ask us to delete it. We then delete it from all of our systems, including the
            AI teacher&rsquo;s working files, and from backups within six months, and we tell our processors to delete
            it too.</li>
          <li><strong>Withdraw consent.</strong> We collect consumer health data only to provide the lessons you ask
            for, never on a separate consent. You can stop at any time by deleting your requests or your account.</li>
        </ul>
        <p>
          <strong>To make a request,</strong> email <Contact /> from the address you sign in with, so we can confirm it is
          you. There is no charge. We respond within 45 days of receiving your request. If we need longer, we will tell
          you within those 45 days, and take at most 45 more.
        </p>
        <p>
          <strong>To appeal</strong> a decision not to act on your request, reply to our decision and say you are
          appealing. We will decide within 45 days and tell you in writing what we decided and why. If we deny your
          appeal, you can contact the Washington State Attorney General at{' '}
          <a href={AG_COMPLAINT_URL} rel="noopener noreferrer" target="_blank">{AG_COMPLAINT_URL.replace('https://', '')}</a>.
        </p>
      </Section>
    </LegalPage>
  );
}
