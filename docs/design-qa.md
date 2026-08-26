# AIQYN design QA

## Evidence

- Source visual truth:
  - `C:/Users/Acer/Desktop/smart citi/докмументы/WhatsApp Image 2026-08-13 at 16.47.05 (1).jpeg` — day camera HUD with detections.
  - `C:/Users/Acer/Desktop/smart citi/докмументы/WhatsApp Image 2026-08-13 at 16.47.05 (2).jpeg` — matching raw road frame.
  - `C:/Users/Acer/Desktop/smart citi/докмументы/WhatsApp Image 2026-08-13 at 17.08.36.jpeg` — night HUD state.
  - `C:/Users/Acer/Desktop/smart citi/докмументы/WhatsApp Image 2026-08-13 at 18.09.57.jpeg` — AIQYN brand asset.
- Browser-rendered implementation:
  - `C:/Users/Acer/Desktop/smart citi/aiqyn/audit/06-portal-final-lines.png` — dashboard with road-aligned yellow/red segments.
  - `C:/Users/Acer/Desktop/smart citi/aiqyn/audit/04-portal-final-detail.png` — draft/operator detail state.
  - `C:/Users/Acer/Desktop/smart citi/aiqyn/audit/07-camera-final.jpg` — live day HUD on the provided raw frame.
  - `C:/Users/Acer/Desktop/smart citi/aiqyn/audit/09-camera-detection-final.jpg` — real local-model detection state.
- Same-input comparisons opened and inspected:
  - `C:/Users/Acer/Desktop/smart citi/aiqyn/audit/08-camera-design-qa.png` — source HUD (left) and rendered HUD (right).
  - `C:/Users/Acer/Desktop/smart citi/aiqyn/audit/10-portal-design-qa.png` — portal before (left) and after (right).
  - `C:/Users/Acer/Desktop/smart citi/aiqyn/audit/11-detail-design-qa.png` — detail before (left) and after (right).

## Viewport and normalization

- Portal state: desktop dark operator dashboard, default `new` filter, incidents/roadwork/closure layers enabled; detail capture uses an unsubmitted draft.
- Portal screenshot bitmap and capture viewport: 1280 × 720 px, desktop, 1× capture density.
- Camera source pixels: 1122 × 1402 px; normalized to 960 × 1200 px for the comparison.
- Camera implementation pixels: 960 × 1200 px at 1× output density.
- Camera comparison: 1920 × 1200 px, equal 960 × 1200 panels, no crop mismatch or device frame.

## Full-view comparison evidence

- The source and implementation retain the same black top bar, REC/red status dot, left/center/right metadata rhythm, cyan perspective lines, square lane nodes, restrained glow, and unobstructed road image.
- The portal before/after comparison shows a materially clearer operator hierarchy: stable header statistics, searchable queue, grouped incident pins, visible uncertainty state, separate layer controls, and road-aligned yellow/red geometry instead of only large circular zones.
- The detail before/after comparison shows an explicit unsubmitted-draft banner, coordinate uncertainty warning, operator correction action, separated risk/confidence fields, and official lifecycle actions.

## Focused region comparison evidence

- HUD focus: `08-camera-design-qa.png` makes the top bar, lane vanishing point, edge clamping, line weight, node spacing, and cyan token directly readable. The implementation matches the intended visual language; the raw input has no physical defects, so omitting synthetic boxes is correct.
- Detection focus: `09-camera-detection-final.jpg` verifies that a real 52% local-model pothole candidate gets a clipped, upright, numbered red box without moving to a later camera frame.
- Portal focus: `11-detail-design-qa.png` verifies the draft/uncertain-location states above the evidence image. No additional crop was required because the high-impact banners and actions are visible in the browser capture and were also confirmed in the DOM snapshot.

## Required fidelity surfaces

- Fonts and typography: Segoe UI/Arial-compatible system fonts match the source's neutral technical HUD; weights, compact all-caps eyebrow text, line height, and hierarchy remain readable in Kazakh and Russian. The OpenCV/Pillow HUD uses installed Segoe UI, avoiding missing Kazakh glyphs.
- Spacing and layout rhythm: top bar regions, sidebar density, map overlays, 44 px anchored pins, drawer sections, and action spacing are consistent. The initial map-title/zoom collision was removed by shifting the title card.
- Colors and visual tokens: `#00E5FF` camera lanes, warm yellow roadwork, red closure/incident, charcoal panels, and restrained state fills match the reference semantics with accessible foreground contrast.
- Image quality and asset fidelity: the supplied AIQYN raster logo is reused directly; the road photograph is not regenerated or replaced. The camera preview keeps the original 960 × 1200 content sharp and only adds the production overlay.
- Copy and content: drafts are named as drafts; confidence is explicitly not severity; GPS uncertainty and operator correction are visible; external ticket ID, responsible party, due date request, after-photo, reinspection, closure, and reopening states are represented.
- Icons and affordances: one Font Awesome family is used consistently; labels/ARIA names accompany icon-only controls; active layers, disabled draw actions, hover/focus, reduced-motion, and approximate-pin states are implemented.

## Findings

- No actionable P0/P1/P2 visual or interaction mismatch remains.
- [P3] Night-feed visual coverage is based on the supplied reference plus automated geometry/banner checks, not a new live night camera capture.
  - Location: camera night state.
  - Evidence: the supplied night reference was opened; the current live test source is daylight.
  - Impact: no core workflow impact; final lamp-box visibility still needs a field night feed.
  - Follow-up: capture one real night drive after camera/GPS permission is enabled and retain it as a regression fixture.
- [P3] The browser runtime did not provide a separate mobile viewport capture in this QA session.
  - Location: responsive portal breakpoints.
  - Evidence: desktop DOM/capture passed; responsive CSS at 1350/1050/760 px is present and structurally reviewed.
  - Impact: low for tomorrow's desktop operator demo.
  - Follow-up: add a 390 × 844 visual regression capture when a resizable browser surface is available.

## Comparison history

1. Initial portal pass — blocked.
   - Earlier P1: roadwork data was rendered as large circles and could suppress unrelated road events.
   - Earlier P1: incident positions used raw/fallback GPS with no operator correction or visible uncertainty state.
   - Earlier P1: the document view could read as operator-verified before approval and displayed legacy official copy.
   - Fixes: polyline/corridor zone contract; yellow/red road rendering; `boundary_verified` gate; exact anchored pins; grouped coordinates; audited location correction; draft/approval/ticket states; safe official copy.
   - Post-fix evidence: `06-portal-final-lines.png`, `04-portal-final-detail.png`, and DOM checks for draft copy/ticket request.
2. Initial camera geometry pass — blocked.
   - Earlier P1: the right lane endpoint could leave the frame; missing frames retained stale lanes; high-resolution evidence could be from a later camera moment than its box.
   - Fixes: in-frame perspective validation, confidence and stale TTL, per-image reset, exact detection-frame evidence, optical-flow propagation, Unicode image I/O, and synchronized timestamp/GPS.
   - Post-fix evidence: `07-camera-final.jpg`, `08-camera-design-qa.png`, and `09-camera-detection-final.jpg`.
3. First browser render — blocked.
   - Earlier P2: the map title overlapped Leaflet zoom controls.
   - Fix: moved the title card clear of the zoom stack and preserved the mobile offset.
   - Post-fix evidence: `06-portal-final-lines.png` and `10-portal-design-qa.png`.
4. Final pass — passed.
   - The camera and portal comparison inputs were reopened after fixes.
   - Primary interactions tested: dashboard load, search filtering, layer off/on state, incident detail, operator login, two-point road drawing, official zone modal fields, and yellow/red zone rendering.
   - Browser console errors checked via `tab.dev.logs`: none (`[]`).
   - API/behavior tests: nine stdlib regression tests, Python compileall, JavaScript syntax check, portal health, Vision doctor, Unicode preview render, and idempotent data migrations.

## Implementation checklist

- [x] Road-aligned yellow work and red closure geometry.
- [x] Exact upright marker anchor plus uncertainty/correction workflow.
- [x] Draft → ticket → work → after-photo → reinspection → close/reopen lifecycle.
- [x] Official bilingual copy and current roads-authority naming.
- [x] Source-matched camera HUD and exact-frame annotations.
- [x] Browser/API/model smoke tests and saved visual evidence.

## Follow-up polish

- Add a real night-drive fixture and a resizable mobile screenshot when those physical/browser conditions are available.

final result: passed
