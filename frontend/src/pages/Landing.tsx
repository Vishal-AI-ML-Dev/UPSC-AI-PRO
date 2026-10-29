import { Link } from "react-router-dom"
import { ThemeToggle } from "../components/ThemeToggle"

const STEPS: { icon: string; step: string; title: string; desc: string }[] = [
  {
    icon: "\u{1F9ED}",
    step: "STEP 1",
    title: "Set your target",
    desc: "Tell the Planner your target year, daily hours and weak areas \u2014 your plan is built around your goal.",
  },
  {
    icon: "\u{1F4D6}",
    step: "STEP 2",
    title: "Learn with grounded AI",
    desc: "Notes, current affairs, NCERT, lectures and your own PDFs \u2014 all grounded in real sources.",
  },
  {
    icon: "\u{1F3C6}",
    step: "STEP 3",
    title: "Practice & improve",
    desc: "Solve PYQs, write mains answers and get instant evaluation plus mentor guidance to keep improving.",
  },
]

const TOOLS: { icon: string; step: string; title: string; desc: string }[] = [
  {
    icon: "\u{1F9E0}",
    step: "Step 1",
    title: "AI Mentor",
    desc: "UPSC strategy, doubt-clearing and your full roadmap \u2014 the best place to start.",
  },
  {
    icon: "\u{1F4DD}",
    step: "Step 2",
    title: "Planner",
    desc: "A personalised study plan built around your target year, daily hours and weak areas.",
  },
  {
    icon: "\u{1F4DA}",
    step: "Step 3",
    title: "NCERT Study",
    desc: "Build your foundation chapter by chapter with AI notes and a RAG-powered chat.",
  },
  {
    icon: "\u{1F3A5}",
    step: "Step 4",
    title: "Lecture AI",
    desc: "Turn a YouTube lecture into structured notes and ask follow-up questions about it.",
  },
  {
    icon: "\u{1F4F0}",
    step: "Step 5",
    title: "Current Affairs",
    desc: "Daily grounded news, editorials and monthly digests \u2014 from PIB, The Hindu and Indian Express.",
  },
  {
    icon: "\u{1F4C4}",
    step: "Step 6",
    title: "My PDFs",
    desc: "Upload Laxmikanth, Spectrum or your own notes \u2014 get AI notes and chat with your material.",
  },
  {
    icon: "\u2753",
    step: "Step 7",
    title: "PYQ Practice",
    desc: "Prelims MCQs and Mains \u2014 AI-generated or from your own PYQ PDFs, with instant explanations.",
  },
  {
    icon: "\u270D\uFE0F",
    step: "Step 8",
    title: "Evaluator",
    desc: "Write full answers and get instant feedback on structure, content and marks.",
  },
]

const SOURCES = [
  "PIB",
  "The Hindu",
  "Indian Express",
  "NCERT",
  "Down To Earth",
  "Your own PDFs",
]

const FAQ: { q: string; a: string }[] = [
  {
    q: "Is UPSC AI free to use?",
    a: "Yes. Sign up and start for free \u2014 no credit card needed. You get notes, current affairs, PYQ practice and evaluation right away.",
  },
  {
    q: "Does the AI make up facts?",
    a: "It\u2019s built specifically to avoid that. Notes, current affairs and questions are grounded on real sources (your PDFs, NCERT, and feeds like PIB, The Hindu and Indian Express), and uncertain figures are clearly marked \u201c(verify)\u201d. It won\u2019t present guesses as certainty.",
  },
  {
    q: "What can I study here?",
    a: "NCERT chapters, your own uploaded books/notes, daily and monthly current affairs, previous-year-style question practice, mains answer evaluation, and a personalised study planner with live exam dates.",
  },
  {
    q: "Where do the current affairs come from?",
    a: "Daily current affairs are grounded in real headlines from trusted feeds \u2014 PIB, The Hindu, Indian Express and Down To Earth. If reliable news isn\u2019t found, we tell you honestly instead of fabricating a digest.",
  },
  {
    q: "Can I upload my own notes or books?",
    a: "Yes. Upload a text-based PDF and the AI generates grounded notes and lets you chat with the document. You can also build a personal question bank from your own PDFs.",
  },
  {
    q: "Is my data private?",
    a: "Each account has its own secure login, and your history and uploads stay tied to your account. Passwords are encrypted and never stored in plain text.",
  },
]

function Brand() {
  return (
    <Link to="/" className="flex items-center gap-2 font-extrabold">
      <span className="grid h-8 w-8 place-items-center rounded-lg text-white shadow-[0_4px_14px_var(--glow)] [background:linear-gradient(135deg,var(--brand),var(--accent))]">
        <span className="text-sm">U</span>
      </span>
      <span>UPSC&nbsp;AI</span>
    </Link>
  )
}

export function Landing() {
  return (
    <div className="min-h-screen">
      {/* NAV */}
      <header className="sticky top-0 z-30 border-b border-border bg-bg/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
          <Brand />
          <nav className="flex items-center gap-2 sm:gap-3">
            <a
              href="#how"
              className="hidden px-2 text-sm font-medium text-muted hover:text-fg sm:block"
            >
              How it works
            </a>
            <a
              href="#faq"
              className="hidden px-2 text-sm font-medium text-muted hover:text-fg sm:block"
            >
              FAQ
            </a>
            <ThemeToggle />
            <Link to="/login" className="btn btn-ghost text-sm">
              Sign in
            </Link>
          </nav>
        </div>
      </header>

      {/* HERO */}
      <section className="relative overflow-hidden">
        <div className="pointer-events-none absolute left-1/2 top-0 h-[520px] w-[760px] -translate-x-1/2 [background:radial-gradient(circle_at_center,var(--glow),transparent_68%)]" />
        <div className="relative mx-auto max-w-3xl px-4 py-20 text-center">
          <span className="inline-flex items-center gap-2 rounded-full border border-border bg-surface px-4 py-1.5 text-sm font-semibold text-muted shadow-card">
            <span className="h-1.5 w-1.5 rounded-full bg-accent shadow-[0_0_0_3px_color-mix(in_srgb,var(--accent)_28%,transparent)]" />
            Built on real sources &mdash; never guesses
          </span>
          <h1 className="mx-auto mt-6 text-4xl font-extrabold leading-tight sm:text-6xl">
            Your UPSC prep,
            <br />
            <span className="brand-text">grounded in truth.</span>
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-lg text-muted">
            Notes, current affairs, PYQ practice and mains evaluation &mdash; grounded
            in real sources, with honest &ldquo;verify&rdquo; flags whenever a fact
            isn&rsquo;t certain. No confident guessing.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            <Link to="/login" className="btn btn-brand px-6 py-3 text-base">
              Start free
            </Link>
            <a href="#how" className="btn btn-ghost px-6 py-3 text-base">
              See how it works
            </a>
          </div>
          <p className="mt-4 text-sm text-muted">
            <span className="text-success">&#10003;</span> Free to start
            &nbsp;&middot;&nbsp; No credit card needed
          </p>

          <div className="mt-12">
            <p className="text-xs font-semibold uppercase tracking-wider text-muted/80">
              Grounded in trusted sources
            </p>
            <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
              {SOURCES.map((s) => (
                <span
                  key={s}
                  className="rounded-lg border border-border bg-surface px-4 py-2 text-sm font-semibold"
                >
                  {s}
                </span>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* HOW IT WORKS */}
      <section id="how" className="mx-auto max-w-6xl px-4 py-16">
        <div className="text-center">
          <div className="brand-text text-sm font-bold uppercase tracking-wider">
            How It Works
          </div>
          <h2 className="mt-2 text-3xl font-extrabold">How UPSC AI works for you</h2>
          <p className="mx-auto mt-3 max-w-2xl text-muted">
            Three simple steps &mdash; from setting your goal to evaluated practice. The
            full toolbox is just below.
          </p>
        </div>
        <div className="mt-10 grid grid-cols-1 gap-5 sm:grid-cols-3">
          {STEPS.map((s) => (
            <div
              key={s.title}
              className="card group p-7 transition hover:-translate-y-1 hover:border-brand-400"
            >
              <div className="grid h-12 w-12 place-items-center rounded-xl bg-surface2 text-2xl">
                {s.icon}
              </div>
              <div className="mt-4 text-xs font-bold uppercase tracking-wider text-muted">
                {s.step}
              </div>
              <h3 className="mt-1 text-lg font-bold">{s.title}</h3>
              <p className="mt-2 text-sm text-muted">{s.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* TOOLS */}
      <section id="tools" className="border-y border-border bg-surface/50">
        <div className="mx-auto max-w-6xl px-4 py-16">
          <div className="text-center">
            <div className="brand-text text-sm font-bold uppercase tracking-wider">
              Everything you get
            </div>
            <h2 className="mt-2 text-3xl font-extrabold">
              8 AI tools &mdash; your full study journey
            </h2>
            <p className="mx-auto mt-3 max-w-2xl text-muted">
              From strategy to answer evaluation &mdash; everything in one place. Follow
              the order below for best results.
            </p>
          </div>
          <div className="mt-10 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {TOOLS.map((t) => (
              <div
                key={t.title}
                className="card p-6 transition hover:-translate-y-1 hover:border-brand-400"
              >
                <div className="grid h-11 w-11 place-items-center rounded-xl bg-surface2 text-xl">
                  {t.icon}
                </div>
                <div className="mt-4 text-xs font-bold uppercase tracking-wider text-accent">
                  {t.step}
                </div>
                <h3 className="mt-1 font-bold">{t.title}</h3>
                <p className="mt-2 text-sm text-muted">{t.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* FAQ */}
      <section id="faq" className="mx-auto max-w-3xl px-4 py-16">
        <div className="text-center">
          <div className="brand-text text-sm font-bold uppercase tracking-wider">
            FAQ
          </div>
          <h2 className="mt-2 text-3xl font-extrabold">Everything you need to know</h2>
          <p className="mx-auto mt-3 max-w-2xl text-muted">
            Common questions about UPSC AI and how it helps your preparation.
          </p>
        </div>
        <div className="mt-8 space-y-3">
          {FAQ.map((f, i) => (
            <details
              key={f.q}
              open={i === 0}
              className="group card overflow-hidden p-0"
            >
              <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-5 py-4 font-semibold">
                <span>{f.q}</span>
                <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-surface2 text-muted transition group-open:rotate-45">
                  +
                </span>
              </summary>
              <div className="border-t border-border px-5 py-4 text-sm text-muted">
                {f.a}
              </div>
            </details>
          ))}
        </div>
      </section>

      {/* CTA STRIP */}
      <section className="mx-auto max-w-6xl px-4 pb-16">
        <div className="relative overflow-hidden rounded-3xl px-6 py-16 text-center text-white [background:linear-gradient(135deg,var(--brand),var(--brand-2)_60%,var(--accent))]">
          <h2 className="text-3xl font-extrabold">Start preparing smarter today</h2>
          <p className="mx-auto mt-3 max-w-xl text-white/90">
            Join free and let an honest AI study partner handle notes, current affairs
            and practice.
          </p>
          <div className="mt-7 flex justify-center">
            <Link
              to="/login"
              className="btn bg-white px-6 py-3 text-base font-bold text-brand hover:opacity-90"
            >
              Get started for free
            </Link>
          </div>
        </div>
      </section>

      {/* FOOTER */}
      <footer className="border-t border-border">
        <div className="mx-auto grid max-w-6xl grid-cols-1 gap-8 px-4 py-10 sm:grid-cols-3">
          <div className="max-w-sm">
            <Brand />
            <p className="mt-3 text-sm text-muted">
              An honest AI study partner for UPSC aspirants &mdash; grounded notes,
              current affairs, PYQs and mains evaluation, built to be upfront about what
              it knows.
            </p>
          </div>
          <div>
            <h4 className="font-bold">Product</h4>
            <div className="mt-3 flex flex-col gap-2 text-sm text-muted">
              <a href="#how" className="hover:text-fg">
                How it works
              </a>
              <a href="#faq" className="hover:text-fg">
                FAQ
              </a>
              <Link to="/login" className="hover:text-fg">
                Sign in
              </Link>
            </div>
          </div>
          <div>
            <h4 className="font-bold">Connect</h4>
            <div className="mt-3 flex flex-col gap-2 text-sm text-muted">
              <a href="mailto:vishalshivhare.ai@gmail.com" className="hover:text-fg">
                vishalshivhare.ai@gmail.com
              </a>
            </div>
          </div>
        </div>
        <div className="border-t border-border">
          <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-2 px-4 py-5 text-sm text-muted sm:flex-row">
            <span>&copy; {new Date().getFullYear()} UPSC AI. All rights reserved.</span>
            <span>Made for aspirants, with honest AI.</span>
          </div>
        </div>
      </footer>
    </div>
  )
}
