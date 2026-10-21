/** Open /tests/metric-grid.html with Vite. Uses production cards and real React DOM updates. */
import { useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { flushSync } from "react-dom";
import { CARDS } from "../src/cards/registry";
import type { CardContext } from "../src/cards/spec";
import { prepareLanguage, setLanguage } from "../src/lib/i18n";
import type { RangeKey, Usage } from "../src/lib/types";
import { Card } from "../src/ui/kit";
import "../src/styles.css";

const ranges: RangeKey[] = ["today", "week", "month", "year", "all"];

function context(step: number): CardContext {
  const phase = step % 6;
  const perf = phase === 3 ? null : {
    tps: 30 + step, n: 3, o: 900, g: 900 / (30 + step),
    ttft: phase === 1 || phase === 4 ? null : 1.2 + step / 10,
    tn: phase === 1 || phase === 4 ? 0 : 2, en: phase === 4 || phase === 5 ? 1 : 0,
  };
  const r = {
    in: 1000 + step, out: 200 + step, cr: phase % 2 ? 0 : 300, cw: phase === 2 ? 100 : 0,
    cached: phase % 2 ? 0 : 300, reason: phase === 1 ? 70 : 0, thoughts: phase === 2 ? 30 : 0,
    hit: phase % 2 ? 0 : 96, cost: 1 + step / 10, credits: phase % 2 ? 0 : 2,
    sessions: 2, calls: 3, turns: 4, tools: 5, duration: 30, tasks: 2,
    completed: 1, failed: 1, requests: 3, messages: 4, sub_agents: 1,
    tokens: 1600 + step, total_tokens: 1600 + step, usage_available: true, usage_calls: 3,
    ttft: 1300, response: 3300, errors: 0, cancellations: 0,
    ctx_used: 1000, ctx_window: 4000, ctx: 25, models: [], perf,
  };
  const allRanges = Object.fromEntries(["today", "yesterday", "week", "last_week", "month", "year", "all"].map((key) => [key, r]));
  const quota = {
    available: true, plan: "Fixture", usage: { ranges: allRanges },
    windows: phase % 2 ? [{ id: "week", title: "Week", used_pct: step }] : [
      { id: "five", title: "5h", used_pct: 50 }, { id: "week", title: "Week", used_pct: step },
    ],
    details: phase % 2 ? [] : [{ label: "Fixture", value: "Synthetic" }],
  };
  const usage: Record<string, unknown> = {};
  for (const key of ["claude", "codex", "gemini", "grok", "grok_bot", "qoder", "qoderwork", "qoder_ide", "qodercli", "qodercli_cn", "hermes", "zcode", "mimocode", "openclaw", "pi", "prime_agent", "workbuddy", "workbuddy_ai", "codebuddy", "deepseek_harness", "opencode", "qwencode", "devin", "minimax", "kimicode", "musecode", "cmdcode"]) {
    usage[key] = { ranges: allRanges, quota, model: "fixture", session_name: "fixture", session_total: 1200, reserve_ranges: allRanges };
  }
  for (const key of ["cursor", "zed", "sub2api", "zai"]) usage[key] = quota;
  usage.qwenwork = {
    available: true, remaining: 100 + step, remaining_pct: 50,
    segments: phase % 2 ? [] : [{ id: "plan_credits", kind: "plan_credits", remaining: 100 + step, total: 1000, used: 900 - step, unit: "credits" }],
    source: "fixture",
  };
  return {
    usage: usage as Usage, range: ranges[step % ranges.length],
    rangeLabel: step % 2 ? "本周" : "今日", refreshing: false,
    pref: (_key, fallback) => fallback,
  };
}

function Cards({ step }: { step: number }) {
  const ctx = context(step);
  return <>{CARDS.map((card) => <section key={card.id} data-card-id={card.id} style={{ padding: 6 }}>
    <Card tint={card.tint}>{card.render(ctx)}</Card>
  </section>)}</>;
}

function App() {
  const [status, setStatus] = useState("Ready — production cards with synthetic usage only");
  const [running, setRunning] = useState(false);
  const [preview, setPreview] = useState("all");
  const liveRoot = useRef<Root | null>(null);
  async function run() {
    setRunning(true);
    setStatus("Running…");
    const live = document.getElementById("live")!;
    const reference = document.getElementById("reference")!;
    const root = liveRoot.current ??= createRoot(live);
    let fresh: Root | null = null;
    const errors: string[] = [];
    const original = console.error;
    console.error = (...args: unknown[]) => { errors.push(args.map(String).join(" ")); original(...args); };
    let checks = 0;
    try {
      for (const language of ["zh", "en"] as const) {
        await prepareLanguage(language);
        setLanguage(language);
        for (let step = 0; step < 30; step++) {
          live.style.width = `${step % 2 ? 440 : 320}px`;
          reference.style.width = live.style.width;
          flushSync(() => root.render(<Cards step={step} />));
          fresh = createRoot(reference);
          flushSync(() => fresh!.render(<Cards step={step} />));
          for (const card of CARDS) {
            const selector = `[data-card-id="${card.id}"]`;
            const actual = live.querySelector(selector)!;
            const expected = reference.querySelector(selector)!;
            if (actual.textContent !== expected.textContent) throw new Error(`${language}/${step}/${card.id}: stale or missing cell`);
            for (const grid of actual.querySelectorAll("[data-metric-grid]")) {
              const ids = [...grid.querySelectorAll("[data-metric]")].map((cell) => cell.getAttribute("data-metric")!);
              if (new Set(ids).size !== ids.length) throw new Error(`${language}/${step}/${card.id}: duplicate metric`);
              for (const icon of ["gauge.medium", "timer"]) {
                const matching = ids.map((id) => JSON.parse(id) as string[]).filter(([name, label]) => name === icon && (icon !== "timer" || label.includes("TTFT")));
                if (matching.length > 1) throw new Error(`${language}/${step}/${card.id}: duplicate speed or TTFT`);
              }
            }
            checks++;
          }
          flushSync(() => fresh!.unmount());
          fresh = null;
          await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
        }
      }
      if (errors.length) throw new Error(errors.join("\n"));
      setStatus(`PASS — ${CARDS.length} production cards, ${checks} refresh checks, 2 languages, 320/440px, no duplicate or stale metrics`);
    } catch (error) {
      setStatus(`FAIL after ${checks} checks — ${String(error)}`);
    } finally {
      console.error = original;
      if (fresh) flushSync(() => fresh!.unmount());
      flushSync(() => root.render(<Cards step={4} />));
      setRunning(false);
    }
  }
  return <main style={{ padding: 24, color: "#fff", background: "#22262d", minHeight: "100vh", fontFamily: "system-ui" }}>
    <h1>Card refresh regression</h1>
    <p role="status" style={{ maxWidth: 840 }}>{status}</p>
    <button onClick={run} disabled={running}>Run all card refresh checks</button>
    <label style={{ display: "block", marginTop: 12 }}>Preview card <select value={preview} onChange={(event) => setPreview(event.target.value)}>
      <option value="all">All cards</option>
      {CARDS.map((card) => <option key={card.id} value={card.id}>{typeof card.name === "string" ? card.name : card.name()}</option>)}
    </select></label>
    <p>Synthetic usage. No collector, account access or network requests.</p>
    {preview !== "all" && <style>{`#live > [data-card-id] { display: none; } #live > [data-card-id="${preview}"] { display: block; }`}</style>}
    <div id="live" style={{ width: 440 }} />
    <div id="reference" aria-hidden style={{ position: "absolute", left: -10000, width: 440 }} />
  </main>;
}

createRoot(document.getElementById("app")!).render(<App />);
