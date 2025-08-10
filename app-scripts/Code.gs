/** ===========================================================
 * Contract Assistant (Minimal Spec: section + exact line)
 * - Preview/Apply based on {section, orig_text, replace_text}
 * - Verify anchors for a batch of patches
 * - Menu + Sidebar bootstrap
 * - Config + Mock data + helpers
 * =========================================================== */

/** ---------- Minimal config (toggle mock vs real here) ---------- **/
const CONFIG = {
  SERVER_URL: '<>', // ngrok
  DOC_URL: '<Your URL HERE>',
  USE_MOCK: false // << set to false to call the real server
};

function getConfig() {
  return CONFIG;
}

/** ---------- Mock data (centralized here) ---------- **/
const MOCK_PLAN = {
  "schema_version":"1.0",
  "plan_id":"plan-2025-08-10-enterprise-finance-de",
  "preamble":{
    "summary":"Align key terms to Enterprise Finance controls: Germany jurisdiction, EU residency, 99.5% uptime, 24x7 support, service credits applicable to professional services, and 24-hour breach notice.",
    "considerations":[
      "Schedule A shows business-hours support; it should be updated separately to reflect 24x7 for consistency.",
      "Arbitration moved to DIS in Germany to align with German governing law.",
      "DPA updated to EU residency with SCCs; ensure operational capability for EU-only processing."
    ]
  },
  "patches":[
    {"id":"p-credits-def","section":"## 1. Definitions","segment":"Service Credit definition","orig_text":"- **Service Credit**: A credit calculated under §5 and applied to monthly **subscription fees**.  ","replace_text":"- **Service Credit**: A credit calculated under §5 and applied to monthly **subscription fees** and **professional services fees**.  ","rationale":"Enable Service Credits to apply to professional services per control CreditsApplyToPS: Yes.","topic":"Service Credits","choice_group":"credits_application"},
    {"id":"p-credits-fees","section":"## 4. Fees and Payment","segment":"Credits line","orig_text":"- **Credits:** Unless expressly stated otherwise, Service Credits offset **subscription fees only** and do not apply to taxes or professional services fees.  ","replace_text":"- **Credits:** Unless expressly stated otherwise, Service Credits offset **subscription fees** and **professional services fees**, and do not apply to taxes.  ","rationale":"Reflect control that credits may be applied to professional services (CreditsApplyToPS: Yes).","topic":"Service Credits","choice_group":"credits_application"},
    {"id":"p-sla-uptime","section":"## 5. Service Levels (SLA)","segment":"Uptime Target","orig_text":"- **Uptime Target:** Provider targets **99.0%** monthly uptime for production Services.  ","replace_text":"- **Uptime Target:** Provider targets **99.5%** monthly uptime for production Services.  ","rationale":"Set uptime to match control UptimeTarget: 99.5%.","topic":"SLA","choice_group":"uptime_target"},
    {"id":"p-sla-support-hours","section":"## 5. Service Levels (SLA)","segment":"Support Hours","orig_text":"- **Support Hours:** Business hours (local time). Priority 1 issues receive expedited attention during business hours.  ","replace_text":"- **Support Hours:** 24x7. Priority 1 issues are handled 24x7.  ","rationale":"Align support coverage with SupportModel: 24x7.","topic":"Support","choice_group":"support_model"},
    {"id":"p-dpa-breach","section":"## 9. Data Protection Addendum (DPA)","segment":"9.6 Breach Notification","orig_text":"**9.6 Breach Notification.** Provider will notify Customer of a Security Incident affecting Personal Data within **forty-eight (48) hours** of awareness.  ","replace_text":"**9.6 Breach Notification.** Provider will notify Customer of a Security Incident affecting Personal Data within **twenty-four (24) hours** of awareness.  ","rationale":"Reflect BreachNoticeHours: 24 to meet Finance/Enterprise expectations.","topic":"DPA","choice_group":"breach_notice_timing"},
    {"id":"p-dpa-residency","section":"## 9. Data Protection Addendum (DPA)","segment":"9.7 Data Transfers & Residency","orig_text":"**9.7 Data Transfers & Residency.** Provider may process and transfer Personal Data globally subject to lawful transfer mechanisms.  ","replace_text":"**9.7 Data Transfers & Residency.** Provider will process Personal Data in the EU and will not transfer Personal Data outside the EU except on Customer’s documented instructions and subject to lawful transfer mechanisms (e.g., SCCs) and appropriate supplementary measures.  ","rationale":"Enforce EU data residency per Residency: EU and Finance domain needs.","topic":"DPA","choice_group":"data_residency"},
    {"id":"p-law-arbitration","section":"## 11. Governing Law & Dispute Resolution","segment":"Governing law and arbitration","orig_text":"This Agreement is governed by the laws of **New York**, excluding conflict-of-law rules. Disputes unresolved within thirty (30) days of good-faith negotiation will proceed to binding arbitration in **New York, NY** under JAMS rules.  ","replace_text":"This Agreement is governed by the laws of **Germany**, excluding conflict-of-law rules. Disputes unresolved within thirty (30) days of good-faith negotiation will proceed to binding arbitration in **Frankfurt am Main, Germany** under the DIS Rules.  ","rationale":"Align governing law and forum with Jurisdiction: Germany; use DIS for arbitration.","topic":"Jurisdiction","choice_group":"jurisdiction"},
    {"id":"p-support-model","section":"## 14. Support & Named Contacts","segment":"Support coverage line","orig_text":"Support is provided during business hours with initial responses within twenty-four (24) hours for Priority 2–3. Priority 1 issues receive expedited attention during business hours.  ","replace_text":"Support is provided 24x7 with initial responses within twenty-four (24) hours for Priority 2–3. Priority 1 issues are handled 24x7.  ","rationale":"Ensure section-level support language matches SupportModel: 24x7.","topic":"Support","choice_group":"support_model"},
    {"id":"p-csm-name","section":"## 14. Support & Named Contacts","segment":"Named CSM","orig_text":"**Named CSM:** [optional].  ","replace_text":"**Named CSM:** Jane Smith.  ","rationale":"Populate Named Customer Success Manager per control CSMName.","topic":"Support"}
  ]
};

function getMockPlan() {
  return MOCK_PLAN;
}

/** ---------- Menu & Sidebar ---------- **/
function onOpen() {
  DocumentApp.getUi()
    .createMenu('Contract Assistant')
    .addItem('Open panel', 'showSidebar')
    .addToUi();
}

function showSidebar() {
  const html = HtmlService.createHtmlOutputFromFile('Sidebar').setTitle('Contract Assistant');
  DocumentApp.getUi().showSidebar(html);
}

/** ---------- Locators (section + exact line) ---------- **/
function _findSectionRange_(sectionHeading) {
  const body = DocumentApp.getActiveDocument().getBody();
  const n = body.getNumChildren();
  let start = -1, end = n;

  const target = (sectionHeading || '').trim();
  for (let i = 0; i < n; i++) {
    const el = body.getChild(i);
    if (el.getType() !== DocumentApp.ElementType.PARAGRAPH) continue;
    const text = el.asParagraph().getText().trim();
    if (text === target) {
      start = i;
      for (let j = i + 1; j < n; j++) {
        const el2 = body.getChild(j);
        if (el2.getType() !== DocumentApp.ElementType.PARAGRAPH) continue;
        const t2 = el2.asParagraph().getText().trim();
        if (t2.startsWith('## ')) { end = j; break; }
      }
      break;
    }
  }
  if (start < 0) return null;
  return { start, end };
}

function _findParagraphInRange_(range, exactLine) {
  const body = DocumentApp.getActiveDocument().getBody();
  const needle = (exactLine || '').trim();
  for (let i = range.start + 1; i < range.end; i++) {
    const el = body.getChild(i);
    if (el.getType() !== DocumentApp.ElementType.PARAGRAPH) continue;
    const p = el.asParagraph();
    const tx = (p.getText() || '').trim();
    if (tx === needle) return p;
  }
  return null;
}

/** ---------- Actions ---------- **/
function previewPatchByText(patch) {
  try {
    const rng = _findSectionRange_(patch.section);
    if (!rng) return { ok: false, found: false, reason: 'Section not found' };
    const p = _findParagraphInRange_(rng, patch.orig_text);
    if (!p) return { ok: false, found: false, reason: 'Line not found' };

    if (patch.dry) return { ok: true, found: true, text: p.getText() };

    const t = p.editAsText();
    t.setBackgroundColor(0, Math.max(0, t.getText().length - 1), '#fff2b2'); // pale yellow
    return { ok: true, found: true };
  } catch (e) {
    return { ok: false, found: false, reason: String(e) };
  }
}

function applyPatchByText(patch) {
  try {
    const rng = _findSectionRange_((patch.section || '').trim());
    if (!rng) return { ok: false, reason: 'Section not found' };

    // Try to find exact old line
    const p = _findParagraphInRange_(rng, patch.orig_text);
    if (!p) {
      // Maybe it has already been applied somewhere in this section?
      const body = DocumentApp.getActiveDocument().getBody();
      const target = (patch.replace_text || '').trim();
      for (let i = rng.start + 1; i < rng.end; i++) {
        const el = body.getChild(i);
        if (el.getType() !== DocumentApp.ElementType.PARAGRAPH) continue;
        if (el.asParagraph().getText().trim() === target) {
          // It's already there—treat as no-op
          return { ok: true, alreadyApplied: true };
        }
      }
      return { ok: false, reason: 'Line not found' };
    }

    const t = p.editAsText();
    t.setText(patch.replace_text);
    t.setBackgroundColor(0, Math.max(0, t.getText().length - 1), '#d8ffd8'); // light green
    return { ok: true };
  } catch (e) {
    return { ok: false, reason: String(e) };
  }
}

function verifyPatchesByText(patches) {
  const out = [];
  (patches || []).forEach(p => {
    const rng = _findSectionRange_(p.section);
    if (!rng) { out.push({ id: p.id, found: false, where: 'section' }); return; }
    const para = _findParagraphInRange_(rng, p.orig_text);
    out.push({ id: p.id, found: !!para, where: para ? 'line' : 'line-missing' });
  });
  return out;
}

/** Convenience: peek without styling */
function peekPatchByText(patch) {
  try {
    const rng = _findSectionRange_(patch.section);
    if (!rng) return { ok: false, found: false, reason: 'Section not found' };
    const p = _findParagraphInRange_(rng, patch.orig_text);
    if (!p) return { ok: false, found: false, reason: 'Line not found' };
    return { ok: true, found: true, text: p.getText() };
  } catch (e) {
    return { ok: false, found: false, reason: String(e) };
  }
}

function getCurrentDocUrl() {
  return DocumentApp.getActiveDocument().getUrl();
}

