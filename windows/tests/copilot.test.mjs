import assert from "node:assert/strict";
import { after, beforeEach, test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { testRuntime } from "./runtime.mjs";

const runtime = await testRuntime();
after(() => runtime.close());
beforeEach(() => runtime.clear());
const { CARDS } = await runtime.load("src/cards/registry.ts");
const share = await runtime.load("src/lib/share.tsx");
const i18n = await runtime.load("src/lib/i18n.ts");
const context = (range) => ({
  usage: { copilot: { ranges: { today: range } } },
  range: "today", rangeLabel: "今日", refreshing: false, pref: (_key, fallback) => fallback,
});
const card = CARDS.find(item => item.id === "copilot");

test("Copilot is a local token card with its own default-on show setting", () => {
  assert.ok(card);
  assert.equal(card.visibleKey, "showCopilot");
  assert.equal(card.visibleDefault, true);
  assert.equal(share.currentShareVisibility().showCopilot, true);
  assert.equal(card.active(context({ sessions: 1, in: 100 })), true);
  assert.equal(card.active(context({})), false);
  assert.equal(share.shareToolLines(context({}).usage, "today", share.currentShareVisibility()).some(line => line.id === "copilot"), false);
});

test("Copilot card and share show output-only coverage and omit ambiguous counters/cost", () => {
  // Already normalized from raw outputTokens=20, reasoningTokens=7.
  const ctx = context({ sessions: 1, in: 100, cr: 50, cw: 10, out: 13, reason: 7, credits: 2, cost: 0.5, coverage: "output_only", models: [{ name: "test-model", in: 100, cr: 50, cw: 10, out: 13, reason: 7, cost: 0.5, pin: 4, pout: 5 }] });
  const html = renderToStaticMarkup(card.render(ctx));
  assert.match(html, /GitHub Copilot/);
  assert.match(html, /仅输出/);
  assert.match(html, /test-model/);
  assert.doesNotMatch(html, /输入|缓存|成本|\$0\.50/);
  assert.match(html, /Premium Requests/g);
  ctx.usage.copilot_cli = ctx.usage.copilot;
  ctx.usage.github_copilot = ctx.usage.copilot;
  const lines = share.shareToolLines(ctx.usage, "today", share.currentShareVisibility());
  assert.equal(lines.filter(item => item.id === "copilot").length, 1);
  const line = lines.find(item => item.id === "copilot");
  assert.equal(line.tokens, 20);
  assert.equal(line.output, 13);
  assert.equal(line.input, null);
  assert.equal(line.cacheRead, null);
  assert.equal(line.cacheWrite, null);
  assert.equal(line.cost, null);
  assert.match(line.extra, /仅输出/);
  assert.ok(!share.shareLineCells(line, card.tint).some(cell => /输入|缓存|成本/.test(cell.label ?? "")));
  assert.equal(line.reason, 7);
  assert.match(line.extra, /2.*Premium Requests/);
  assert.ok(share.shareLineCells(line, card.tint).some(cell => cell.label === "Premium Requests" && cell.value === "2"));
  assert.equal(share.shareTotals(lines).tokens, 20);
  assert.equal(share.shareTotals(lines).cost, 0);
});

test("turning off Copilot removes it from the shared overview", () => {
  runtime.pref("showCopilot", false);
  const ctx = context({ sessions: 1, in: 100, credits: 2 });
  assert.equal(share.currentShareVisibility().showCopilot, false);
  assert.equal(share.shareToolLines(ctx.usage, "today", share.currentShareVisibility())
    .some(line => line.id === "copilot"), false);
});

test("premium-only Copilot activity renders metrics instead of an empty card", () => {
  const ctx = context({ credits: 2 });
  assert.equal(card.active(ctx), true);
  assert.match(renderToStaticMarkup(card.render(ctx)), /Premium Requests/g);
  const line = share.shareToolLines(ctx.usage, "today", share.currentShareVisibility())
    .find(item => item.id === "copilot");
  assert.match(line.extra, /2.*Premium Requests/);
});

test("Copilot output-only label uses shared translations in cards, models and share lines", async () => {
  for (const [lang, expected] of [["zh", "仅输出"], ["en", "Output only"], ["fr", "Sortie uniquement"], ["ja", "出力のみ"], ["ko", "출력만"]]) {
    runtime.pref("appLanguage", lang);
    await i18n.prepareLanguage(lang);
    const ctx = context({ sessions: 1, out: 13, reason: 7, credits: 2, coverage: "output_only", models: [{ name: "model", out: 13, reason: 7 }] });
    const html = renderToStaticMarkup(card.render(ctx));
    assert.ok(html.split(expected).length >= 3, `${lang}: card and model coverage`);
    const line = share.shareToolLines(ctx.usage, "today", share.currentShareVisibility()).find(line => line.id === "copilot");
    assert.ok(line.extra.startsWith(expected));
    assert.equal(share.shareLineHeadline(line, "today").caption, expected);
    assert.ok(share.shareLineCells(line, card.tint).some(cell => cell.label === "Premium Requests" && cell.value === "2"));
  }
});
