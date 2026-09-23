import { Link } from 'react-router-dom'
import { Helmet } from 'react-helmet-async'
import { Scale } from 'lucide-react'
import { APP_NAME, APP_DOMAIN } from '@/lib/config'

export default function TermsPage() {
  return (
    <>
      <Helmet>
        <title>Terms of Service — {APP_NAME}</title>
        <meta name="description" content={`Terms and conditions governing use of ${APP_NAME}.`} />
        <meta name="robots" content="noindex" />
      </Helmet>
    <div className="min-h-screen bg-zinc-950 text-white">
      <header className="border-b border-zinc-800/60">
        <div className="max-w-3xl mx-auto px-6 py-5 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-amber-400 to-amber-600 flex items-center justify-center shrink-0">
              <Scale className="w-4 h-4 text-zinc-950" strokeWidth={2.5} />
            </div>
            <span className="font-serif font-semibold text-white">{APP_NAME}</span>
          </Link>
          <Link to="/" className="text-xs text-zinc-400 hover:text-white transition-colors">← Back</Link>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-12 space-y-10">
        <div>
          <h1 className="text-3xl font-bold text-white mb-2">Terms of Service</h1>
          <p className="text-sm text-zinc-400">Last updated: September 2026</p>
        </div>

        <section className="bg-amber-900/20 border border-amber-800/40 rounded-xl px-6 py-5 text-sm text-amber-200 leading-relaxed">
          <strong className="text-amber-400">Not legal advice.</strong> {APP_NAME} provides general legal information only. Nothing on this platform constitutes legal advice, and no solicitor-client relationship is formed by using it. For advice about your specific situation, consult a qualified Australian legal practitioner.
        </section>

        <Section title="1. Acceptance">
          <p>By creating an account or using {APP_NAME} ("{APP_DOMAIN}"), you agree to these Terms of Service. If you do not agree, do not use the service.</p>
        </Section>

        <Section title="2. The service">
          <p>{APP_NAME} is a free platform that lets you search NSW legislation and caselaw, ask plain-English legal questions, and receive AI-generated responses grounded in publicly available legal sources.</p>
          <p>The service is intended as a starting point for legal research and general understanding — not as a substitute for professional legal advice.</p>
        </Section>

        <Section title="3. Eligibility">
          <p>You must be at least 18 years old to create an account. By registering, you confirm that you are 18 or older.</p>
        </Section>

        <Section title="4. Your account">
          <ul>
            <li>You are responsible for maintaining the confidentiality of your login credentials.</li>
            <li>You must provide accurate information when registering.</li>
            <li>You may not share your account with others or create accounts on behalf of third parties without consent.</li>
            <li>Notify us immediately if you suspect unauthorised access to your account.</li>
          </ul>
        </Section>

        <Section title="5. Acceptable use">
          <p>You agree not to:</p>
          <ul>
            <li>Use the service for any unlawful purpose or in violation of any applicable law.</li>
            <li>Submit content that is false, misleading, defamatory, or harassing.</li>
            <li>Attempt to reverse-engineer, scrape, or systematically extract data from the platform.</li>
            <li>Circumvent or interfere with the service's security measures.</li>
            <li>Use the service to generate content intended to mislead others about their legal rights or obligations.</li>
          </ul>
        </Section>

        <Section title="6. Limitation of liability">
          <p>To the maximum extent permitted by Australian law:</p>
          <ul>
            <li>{APP_NAME} is provided "as is" without warranties of any kind, express or implied.</li>
            <li>We do not warrant that AI-generated responses are accurate, complete, current, or applicable to your specific circumstances.</li>
            <li>We are not liable for any loss or damage arising from your reliance on information provided by the service.</li>
            <li>Our total liability to you for any claim is limited to AUD $100.</li>
          </ul>
          <p>Nothing in these terms limits liability for fraud, personal injury caused by negligence, or any liability that cannot be excluded under Australian Consumer Law.</p>
        </Section>

        <Section title="7. Intellectual property">
          <p>The {APP_NAME} platform, including its design, code, and non-legislative content, is owned by us. NSW legislation and caselaw reproduced on the platform is sourced from publicly available government databases and is subject to Crown copyright.</p>
          <p>You retain ownership of any content you submit. By submitting queries, you grant us a limited licence to process that content to provide the service.</p>
        </Section>

        <Section title="8. Third-party services">
          <p>The service uses third-party AI providers (including OpenAI) and infrastructure (AWS). Your use of the service is also subject to their applicable terms.</p>
        </Section>

        <Section title="9. Termination">
          <p>We may suspend or terminate your account if you breach these terms. You may delete your account at any time by contacting us. On termination, your right to use the service ends immediately.</p>
        </Section>

        <Section title="10. Governing law">
          <p>These terms are governed by the laws of New South Wales, Australia. Any disputes are subject to the exclusive jurisdiction of the courts of New South Wales.</p>
        </Section>

        <Section title="11. Changes">
          <p>We may update these terms from time to time. We will notify registered users of material changes by email at least 14 days before they take effect. Continued use after that date constitutes acceptance.</p>
        </Section>

        <Section title="12. Contact">
          <p>Questions about these terms:</p>
          <p className="font-medium text-white">{APP_NAME}<br />
          <a href={`mailto:legal@${APP_DOMAIN}`} className="text-amber-400 hover:underline">{`legal@${APP_DOMAIN}`}</a></p>
        </Section>
      </main>

      <footer className="border-t border-zinc-800/60 mt-12">
        <div className="max-w-3xl mx-auto px-6 py-6 flex items-center justify-between">
          <p className="text-xs text-zinc-500">© 2026 {APP_NAME} · {APP_DOMAIN}</p>
          <div className="flex gap-4">
            <Link to="/privacy" className="text-xs text-zinc-400 hover:text-white transition-colors">Privacy</Link>
            <Link to="/" className="text-xs text-zinc-400 hover:text-white transition-colors">Home</Link>
          </div>
        </div>
      </footer>
    </div>
    </>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold text-white">{title}</h2>
      <div className="text-sm text-zinc-400 leading-relaxed space-y-3 [&_ul]:list-disc [&_ul]:pl-5 [&_ul]:space-y-1.5 [&_a]:text-amber-400 [&_a]:hover:underline [&_strong]:text-zinc-200">
        {children}
      </div>
    </section>
  )
}
