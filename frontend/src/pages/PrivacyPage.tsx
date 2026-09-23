import { Link } from 'react-router-dom'
import { Scale } from 'lucide-react'
import { APP_NAME, APP_DOMAIN } from '@/lib/config'

export default function PrivacyPage() {
  return (
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
          <h1 className="text-3xl font-bold text-white mb-2">Privacy Policy</h1>
          <p className="text-sm text-zinc-400">Last updated: September 2026</p>
        </div>

        <section className="bg-zinc-900/50 border border-zinc-800 rounded-xl px-6 py-5 text-sm text-zinc-300 leading-relaxed">
          This Privacy Policy explains how {APP_NAME} ("{APP_DOMAIN}") collects, uses, and protects your personal information in accordance with the <strong className="text-white">Australian Privacy Act 1988</strong> and the Australian Privacy Principles (APPs).
        </section>

        <Section title="1. What we collect">
          <p>We collect the following personal information:</p>
          <ul>
            <li><strong>Account data</strong> — your full name, email address, and password (bcrypt-hashed; we never store it in plain text) when you register.</li>
            <li><strong>OAuth data</strong> — if you sign in with Google, we receive your name, email address, and Google account ID from Google LLC.</li>
            <li><strong>Usage data</strong> — search queries, questions you submit, and AI responses, to operate the service and improve answer quality.</li>
            <li><strong>Technical data</strong> — IP address, browser type, and access timestamps in server logs for security and debugging.</li>
          </ul>
          <p>We do not collect payment information. The service is free.</p>
        </Section>

        <Section title="2. How we use your information">
          <ul>
            <li>To create and maintain your account.</li>
            <li>To provide legal research responses grounded in NSW legislation and caselaw.</li>
            <li>To send transactional emails (e.g. email verification, password reset).</li>
            <li>To monitor and improve the security and reliability of the platform.</li>
            <li>To comply with legal obligations.</li>
          </ul>
          <p>We do not sell your personal information to third parties. We do not use your data for advertising.</p>
        </Section>

        <Section title="3. Third parties">
          <p>We share data only as necessary to operate the service:</p>
          <ul>
            <li><strong>Amazon Web Services (AWS)</strong> — our infrastructure is hosted on AWS (Sydney region where available). Data is stored and processed on AWS servers subject to AWS's security standards.</li>
            <li><strong>Google LLC</strong> — if you use "Sign in with Google", Google processes your OAuth authentication. See <a href="https://policies.google.com/privacy" target="_blank" rel="noopener noreferrer" className="text-amber-400 hover:underline">Google's Privacy Policy</a>.</li>
            <li><strong>OpenAI</strong> — your legal queries are processed by OpenAI's API to generate responses. Queries are not used to train OpenAI models under our API agreement.</li>
          </ul>
        </Section>

        <Section title="4. Data retention">
          <p>We retain your account data for as long as your account is active. If you request deletion, we will remove your personal information within 30 days, except where retention is required by law.</p>
          <p>Server logs are retained for up to 90 days for security purposes.</p>
        </Section>

        <Section title="5. Security">
          <p>We implement reasonable security measures including:</p>
          <ul>
            <li>Passwords hashed with bcrypt.</li>
            <li>HTTPS enforced on all connections.</li>
            <li>JWT tokens with short expiry and refresh rotation.</li>
            <li>Per-resource authorisation checks to prevent cross-user data access.</li>
            <li>AWS IAM least-privilege policies on all backend functions.</li>
          </ul>
          <p>No system is completely secure. If you discover a security issue, please contact us immediately at the address below.</p>
        </Section>

        <Section title="6. Your rights">
          <p>Under the Australian Privacy Act you have the right to:</p>
          <ul>
            <li>Access the personal information we hold about you.</li>
            <li>Request correction of inaccurate information.</li>
            <li>Request deletion of your account and associated data.</li>
            <li>Complain to the <a href="https://www.oaic.gov.au" target="_blank" rel="noopener noreferrer" className="text-amber-400 hover:underline">Office of the Australian Information Commissioner (OAIC)</a> if you believe we have mishandled your information.</li>
          </ul>
          <p>To exercise these rights, email us at the address in Section 8.</p>
        </Section>

        <Section title="7. Cookies">
          <p>We use only essential cookies and browser localStorage to maintain your login session. We do not use tracking or advertising cookies.</p>
        </Section>

        <Section title="8. Contact">
          <p>For privacy enquiries or to exercise your rights, contact:</p>
          <p className="font-medium text-white">{APP_NAME}<br />
          <a href={`mailto:privacy@${APP_DOMAIN}`} className="text-amber-400 hover:underline">{`privacy@${APP_DOMAIN}`}</a></p>
          <p>We will respond within 30 days.</p>
        </Section>

        <Section title="9. Changes to this policy">
          <p>We may update this policy from time to time. We will notify registered users of material changes by email. Continued use of the service after changes constitutes acceptance of the updated policy.</p>
        </Section>
      </main>

      <footer className="border-t border-zinc-800/60 mt-12">
        <div className="max-w-3xl mx-auto px-6 py-6 flex items-center justify-between">
          <p className="text-xs text-zinc-500">© 2026 {APP_NAME} · {APP_DOMAIN}</p>
          <div className="flex gap-4">
            <Link to="/terms" className="text-xs text-zinc-400 hover:text-white transition-colors">Terms</Link>
            <Link to="/" className="text-xs text-zinc-400 hover:text-white transition-colors">Home</Link>
          </div>
        </div>
      </footer>
    </div>
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
