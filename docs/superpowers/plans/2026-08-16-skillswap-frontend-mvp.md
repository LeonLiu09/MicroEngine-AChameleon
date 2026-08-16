# SkillSwap 2.0 Frontend MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and publish a stable, bilingual, fully interactive SkillSwap hackathon demo as one root-level `index.html`.

**Architecture:** A pinned React 18.3.x/ReactDOM/Babel browser runtime renders a single-file app using a custom hash router, centralized seed data, pure matching helpers, and versioned `localStorage`. CSS tokens and small React components implement the approved Social Gallery visual system; a guarded self-test mode inside the same file verifies pure logic without adding a build system.

**Tech Stack:** HTML5, CSS custom properties, React 18.3.1 UMD, ReactDOM 18.3.1 UMD, Babel Standalone 7.25.6, browser APIs, `localStorage`, custom hash routing, GitHub Pages.

## Global Constraints

- All product HTML, CSS, JSX, translations, mock data, state, and tests must remain in root `index.html`.
- Use pinned React 18.3.1, ReactDOM 18.3.1, and Babel Standalone 7.25.6 CDN versions with `createRoot`.
- Do not add Next.js, React Router, TypeScript, Tailwind tooling, Vite, Webpack, a package manager, or backend code.
- Use `skillswap-mvp-v1` as the only application storage key; never call `localStorage.clear()`.
- `window.location.hash` is the only navigation source of truth.
- Default language is Chinese; the full UI switches to English and persists the choice.
- Preserve B — Social Gallery, the dusty-pink/glass visual system, and two opposite flowing tag rows.
- Mock minors expose only city-level location; no school, precise location, contact information, or real chat.
- P0 behavior is completed and verified before P1 enhancements and P2 motion polish.
- Final delivery is a direct push to `main`, GitHub Pages from `main` root, and a public browser verification.

## File Map

- Create: `index.html` — the complete application and query-guarded self-tests.
- Modify: `README.md` — add final run instructions and deployed Pages URL after deployment succeeds.
- Reference: `docs/superpowers/specs/2026-08-16-skillswap-frontend-design.md` — approved behavior and acceptance source of truth.

---

### Task 1: Failure-safe application shell and self-test runner

**Files:**
- Create: `index.html`

**Interfaces:**
- Produces: `SELF_TEST_MODE: boolean`, `TEST_CASES: Array<{name, run}>`, `test(name, run)`, `runSelfTests()`, `ErrorBoundary`, and the React `createRoot` entry.
- Consumes: none.

- [ ] **Step 1: Write the first boot assertions in the guarded self-test registry**

Add the exact registry before application helpers:

```jsx
const SELF_TEST_MODE = new URLSearchParams(window.location.search).get("selftest") === "1";
const TEST_CASES = [];
function test(name, run) { TEST_CASES.push({ name, run }); }
function assert(condition, message) { if (!condition) throw new Error(message); }

test("storage key is stable", () => {
  assert(STORAGE_KEY === "skillswap-mvp-v1", "Unexpected storage key");
});
test("initial language is Chinese", () => {
  assert(DEFAULT_LANGUAGE === "zh", "Initial language must be zh");
});
```

- [ ] **Step 2: Create the static fallback before any CDN script**

Use this boot shape:

```html
<div id="root">
  <main class="static-fallback" aria-labelledby="fallback-title">
    <p class="fallback-kicker">SKILLSWAP</p>
    <h1 id="fallback-title">正在载入 SkillSwap…</h1>
    <p>如果应用没有出现，请检查网络连接并刷新页面。</p>
    <p lang="en">If the app does not load, check your connection and refresh.</p>
  </main>
</div>
```

Add pinned UMD scripts for React `18.3.1`, ReactDOM `18.3.1`, and Babel Standalone `7.25.6`. Do not remove fallback content manually; `createRoot().render()` replaces it only after all required globals exist.

- [ ] **Step 3: Add the ErrorBoundary and self-test result surface**

Implement these exact contracts:

```jsx
class ErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  componentDidCatch(error, info) { console.error("SkillSwap render error", error, info); }
  render() {
    return this.state.error
      ? <RecoveryScreen onReload={() => location.reload()} onReset={resetDemo} />
      : this.props.children;
  }
}

function runSelfTests() {
  return TEST_CASES.map(({ name, run }) => {
    try { run(); return { name, pass: true, error: "" }; }
    catch (error) { return { name, pass: false, error: error.message }; }
  });
}
```

When `SELF_TEST_MODE` is true, render a semantic pass/fail list instead of `App` and set `document.title` to `SkillSwap Self Tests`.

- [ ] **Step 4: Start a static server and verify the first test fails before constants exist**

Run:

```bash
/Users/andymac/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m http.server 4173
```

Open `http://localhost:4173/?selftest=1#/`. Expected: the test surface reports missing `STORAGE_KEY` or `DEFAULT_LANGUAGE`, proving the test executes.

- [ ] **Step 5: Define boot constants and verify self-test passes**

Add:

```jsx
const STORAGE_KEY = "skillswap-mvp-v1";
const STATE_VERSION = 1;
const DEFAULT_LANGUAGE = "zh";
```

Reload self-test mode. Expected: both initial assertions pass, fallback is replaced, and the normal `#/` route still shows a temporary shell without a blank page.

- [ ] **Step 6: Commit the boot shell**

```bash
git add index.html
git commit -m "feat: add failure-safe SkillSwap shell"
```

---

### Task 2: Centralized translations, seed data, and robust state

**Files:**
- Modify: `index.html` sections `Constants`, `Translations`, `Seed Data`, `State Helpers`, `Self Tests`.

**Interfaces:**
- Produces: `TRANSLATIONS`, `SKILLS`, `MOCK_USERS`, `DEMO_USER`, `SEEDED_REQUESTS`, `SEEDED_CONNECTIONS`, `SKILL_LOOP`, `createInitialState()`, `normalizeState(value)`, `loadState(storage)`, `saveState(state, storage)`, `resetDemo()`, `loginAsDemoUser()`.
- Consumes: `STORAGE_KEY`, `STATE_VERSION`, `DEFAULT_LANGUAGE`.

- [ ] **Step 1: Add failing state tests**

```jsx
test("initial state is versioned and unauthenticated", () => {
  const state = createInitialState();
  assert(state.version === 1, "Wrong state version");
  assert(state.language === "zh", "Wrong language");
  assert(state.isAuthenticated === false, "Fresh state must be logged out");
});
test("invalid storage JSON recovers", () => {
  const storage = { getItem: () => "{bad", setItem() {}, removeItem() {} };
  assert(loadState(storage).version === 1, "Invalid JSON must recover");
});
test("old storage versions recover", () => {
  const storage = { getItem: () => JSON.stringify({ version: 0, isAuthenticated: true }), setItem() {}, removeItem() {} };
  assert(loadState(storage).isAuthenticated === false, "Old version must reset");
});
test("storage exceptions do not crash", () => {
  const storage = { getItem() { throw new Error("denied"); } };
  assert(loadState(storage).version === 1, "Denied storage must recover");
});
```

- [ ] **Step 2: Define normalized state and safe persistence**

Implement:

```jsx
function createInitialState() {
  return {
    version: STATE_VERSION,
    language: DEFAULT_LANGUAGE,
    isAuthenticated: false,
    currentUser: null,
    onboardingCompleted: false,
    onboardingStep: "profile",
    favorites: [],
    sentRequests: [],
    connections: [],
    editedSkills: []
  };
}

function loadState(storage = window.localStorage) {
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return createInitialState();
    const parsed = JSON.parse(raw);
    if (parsed.version !== STATE_VERSION) return createInitialState();
    return normalizeState(parsed);
  } catch (error) {
    console.warn("SkillSwap state recovery", error);
    return createInitialState();
  }
}

function saveState(state, storage = window.localStorage) {
  try { storage.setItem(STORAGE_KEY, JSON.stringify(normalizeState(state))); return true; }
  catch (error) { console.warn("SkillSwap state save failed", error); return false; }
}
```

`normalizeState` must whitelist the schema, allow only `zh`/`en`, require arrays for ID/request collections, and merge safe values over `createInitialState()`.

- [ ] **Step 3: Add immutable bilingual seed records**

Create stable IDs for categories, skills, Daniel, and at least six candidates. Each user must include `id`, `name`, `age`, `city`, `languages`, bilingual `bio`, `skillsOffered`, and `skillsWanted`. Ensure Alice/Daniel/Bob form the specified Photography → Daniel → Chemistry → Bob → Cooking → Alice loop.

Seed requests and connections store IDs rather than duplicate user records. Freeze top-level seed arrays with `Object.freeze` and never mutate them.

- [ ] **Step 4: Centralize every visible string**

Create `TRANSLATIONS.zh` and `.en` with matching key sets and `t(language, key, variables)` interpolation. Add a self-test:

```jsx
test("translation key sets match", () => {
  const zh = Object.keys(TRANSLATIONS.zh).sort().join("|");
  const en = Object.keys(TRANSLATIONS.en).sort().join("|");
  assert(zh === en, "Translation dictionaries diverged");
});
```

- [ ] **Step 5: Implement Daniel login and safe reset**

`loginAsDemoUser()` returns state with Daniel, completed onboarding, seeded favorite IDs, one pending request, and one connection. `resetDemo()` must confirm, call only `localStorage.removeItem(STORAGE_KEY)`, set `location.hash = "#/"`, and reload/reinitialize.

Add tests proving Daniel enters a complete state and a fake storage receives `removeItem("skillswap-mvp-v1")`, never `clear()`.

- [ ] **Step 6: Run self-tests and commit**

Open `http://localhost:4173/?selftest=1#/`. Expected: all state, storage, seed-ID, and translation tests pass.

```bash
git add index.html
git commit -m "feat: add SkillSwap data and state model"
```

---

### Task 3: Custom hash router and centralized guards

**Files:**
- Modify: `index.html` sections `Router`, `Self Tests`, `App`.

**Interfaces:**
- Produces: `parseHash(hash)`, `buildHash(path, query)`, `navigate(path, query)`, `guardRoute(route, state)`, `useHashRoute()`.
- Consumes: `createInitialState()`, `STATE_VERSION`.

- [ ] **Step 1: Add failing router tests**

```jsx
test("dynamic people route extracts id", () => {
  const route = parseHash("#/people/alice-01");
  assert(route.name === "person" && route.params.id === "alice-01", "Dynamic route failed");
});
test("search query parses from hash", () => {
  const route = parseHash("#/search?q=python&level=beginner");
  assert(route.query.q === "python" && route.query.level === "beginner", "Query parse failed");
});
test("logged-out app route redirects to login", () => {
  assert(guardRoute(parseHash("#/discover"), createInitialState()) === "#/login", "Guard failed");
});
```

- [ ] **Step 2: Implement the only navigation source of truth**

`parseHash` returns `{ name, path, params, query, isKnown }`. `buildHash` uses `URLSearchParams`. `navigate` assigns `window.location.hash`. `useHashRoute` listens only to `hashchange`; do not add a competing page state or `pushState`.

- [ ] **Step 3: Implement three-state route guards**

Classify public, onboarding, and application routes in constants. `guardRoute` returns `null` when allowed or one redirect hash when blocked. Incomplete onboarding uses `state.onboardingStep`. Add tests for every route/state combination and for no redirect loop.

- [ ] **Step 4: Add route-level fallbacks**

Unknown routes render Page Not Found. Missing `#/people/:id` data renders User Not Found and Back to Discover. No profile component receives `undefined`.

- [ ] **Step 5: Verify navigation behavior and commit**

In normal mode, navigate Landing → Login → Back → Forward; use self-test mode for parse/guard cases. Expected: URL hash is authoritative and browser history restores each view.

```bash
git add index.html
git commit -m "feat: add guarded hash routing"
```

---

### Task 4: Deterministic matching and search helpers

**Files:**
- Modify: `index.html` sections `Matching Logic`, `Utilities`, `Self Tests`.

**Interfaces:**
- Produces: `getUserById(id)`, `getSkillById(id)`, `scoreComplementaryMatch(current, candidate)`, `findComplementaryMatches(current)`, `findPeerBuddies(current)`, `filterUsers(users, query)`, `getInitials(name)`.
- Consumes: `SKILLS`, `MOCK_USERS` and current-user schema.

- [ ] **Step 1: Add exact match and search expectations**

```jsx
test("Alice is a two-way Daniel match", () => {
  const result = scoreComplementaryMatch(DEMO_USER, getUserById("alice-01"));
  assert(result.isTwoWay === true, "Alice must be a two-way match");
  assert(result.reasons.some(reason => reason.skillId === "photography"), "Photography reason missing");
});
test("match ordering is deterministic", () => {
  const first = findComplementaryMatches(DEMO_USER).map(item => item.user.id).join("|");
  const second = findComplementaryMatches(DEMO_USER).map(item => item.user.id).join("|");
  assert(first === second, "Match order changed");
});
test("search filters teachers by skill", () => {
  const users = filterUsers(MOCK_USERS, { q: "photography", level: "", city: "", lang: "", sort: "best" });
  assert(users.every(user => user.skillsOffered.some(item => item.skillId === "photography")), "Search leaked unrelated users");
});
```

- [ ] **Step 2: Implement complementary score and explanations**

Require at least one candidate-offered/current-wanted overlap. Use fixed points for wanted-skill overlap, reciprocal overlap, level compatibility, shared language, and shared category; cap at 99 and break ties by ID. Return `{ user, score, isTwoWay, reasons }`, where each reason references real skill IDs and a translation key.

- [ ] **Step 3: Implement Peer Buddies and search**

Peer buddies require a shared skill at the same effective level and a shared language. `filterUsers` normalizes case, applies `q`, `level`, `city`, and `lang`, then sorts by deterministic match score or name.

- [ ] **Step 4: Run self-tests and commit**

Expected: Alice is a strong two-way result, every result has a data-backed reason, Peer Buddy constraints hold, filters compose, and repeated runs sort identically.

```bash
git add index.html
git commit -m "feat: add explainable frontend matching"
```

---

### Task 5: Social Gallery design system and shared UI

**Files:**
- Modify: `index.html` sections `Styles`, `Shared Components`.

**Interfaces:**
- Produces: `Button`, `Input`, `Avatar`, `SkillTag`, `SkillEditor`, `SkillLevelSelector`, `UserCard`, `MatchBadge`, `MatchReason`, `Navigation`, `MobileNavigation`, `SearchBar`, `Modal`, `Progress`, `EmptyState`, `ProfileHeader`, `ProfileSkillSection`.
- Consumes: `t`, seed IDs, routing callbacks, current language.

- [ ] **Step 1: Implement exact CSS tokens and global safety rules**

Define ink/paper/deep rose/active rose/soft rose/blush/glass tokens, spacing 4–72 px, radii 16–32 px, pill radius, broad low-opacity glass shadow, focus ring, category colors, `box-sizing`, readable system fallbacks, and a content max width.

Add responsive breakpoints at 768 px and 1024 px, 44 px minimum touch targets, safe-area bottom spacing, and reduced-motion overrides.

- [ ] **Step 2: Implement semantic primitives**

Buttons use `<button>`, inputs have visible labels, skill tags use buttons only when actionable, and Avatar swaps to gradient initials on `onError`. Add bilingual accessible labels to every icon fallback.

- [ ] **Step 3: Implement card, navigation, and empty-state components**

`UserCard` must lead with avatar/name/bio/skills, then MatchBadge/reason, Save, and View Profile. Desktop/tablet navigation and mobile navigation expose the same four destinations. EmptyState always includes a title, explanation, and relevant recovery action.

- [ ] **Step 4: Implement reusable Modal behavior**

Modal closes by explicit button, overlay target, and Escape; uses `role="dialog"`, `aria-modal="true"`, focus containment, and restores the trigger focus. Do not use a third-party modal package.

- [ ] **Step 5: Browser-check component states and commit**

Inspect default, hover, focus, disabled, error, selected, empty, missing-avatar, and narrow-width states. Expected: no clipped text, no broken images, and keyboard-visible actions.

```bash
git add index.html
git commit -m "feat: add SkillSwap social gallery UI"
```

---

### Task 6: Authentication and three-step onboarding

**Files:**
- Modify: `index.html` sections `Page Components`, `App`, `Self Tests`.

**Interfaces:**
- Produces: `LandingPage`, `LoginPage`, `SignupPage`, `ProfileOnboardingPage`, `ShareOnboardingPage`, `LearnOnboardingPage`, `OnboardingCompletePage`, `validateSignup`, `validateProfile`, `validateSkillList`.
- Consumes: state setters, `navigate`, `loginAsDemoUser`, UI primitives, `saveState`.

- [ ] **Step 1: Add validation tests**

Test invalid email, password under eight characters, mismatched password confirmation, missing nickname, empty offered skills, empty wanted skills, duplicate skill IDs, and valid examples.

- [ ] **Step 2: Build Landing, Login, Signup, and Try Demo**

Landing includes bilingual proposition, Login, Create Account, Try Demo, a Social Gallery preview, and a static first version of skill rows. Login/Signup do not transmit data or store reusable passwords. Try Demo enters `#/discover` with Daniel's seeded state.

- [ ] **Step 3: Build profile onboarding**

Avatar selection, nickname, bio, city, language, and age use controlled inputs. Persist the completed step and route to `#/onboarding/share` only after validation.

- [ ] **Step 4: Build offered and wanted skill editors**

Each record has a local stable ID, skill ID, category, description, and valid level. Add/remove works; duplicates warn; the final required skill cannot be removed. Save progression to `onboardingStep` and local state.

- [ ] **Step 5: Build completion summary and verify guards**

Completion shows both lists and Find Skill Partners. Verify refresh on each onboarding route returns to the right step, completed users cannot reopen onboarding, and unauthenticated users cannot open it directly.

- [ ] **Step 6: Commit the account journey**

```bash
git add index.html
git commit -m "feat: add onboarding and demo login"
```

---

### Task 7: Discover, flowing tags, and SkillLoop

**Files:**
- Modify: `index.html` sections `Styles`, `Page Components`.

**Interfaces:**
- Produces: `DiscoverPage`, `SkillMarquee`, `PeerBuddyCard`, `SkillLoopCard`.
- Consumes: matching helpers, UI primitives, `navigate`, current state/language.

- [ ] **Step 1: Build Discover information hierarchy**

Render greeting, search entry, learning goals, complementary recommendations, Peer Buddies, and SkillLoop. Show at least six realistic users across the full seed set. Match reasons must use helper output rather than handwritten unrelated text.

- [ ] **Step 2: Implement seamless opposite tag streams**

Duplicate each row's tag sequence once for a continuous CSS translation loop. Use 26- and 32-second linear animations, opposite directions, hover/focus pause, slightly slower perceived mobile motion, and no jump at the loop boundary.

Each Discover tag navigates to `#/search?q=<skill-slug>`. Under reduced motion, animation is `none`, the duplicate sequence is hidden from assistive technology and display, and the remaining tags wrap statically.

- [ ] **Step 3: Implement the three-person loop**

Render Alice → Daniel Photography, Daniel → Bob Chemistry, and Bob → Alice Cooking as labeled directed relationships with the seeded shared time. Add explicit “demo match” wording; do not describe it as live AI.

- [ ] **Step 4: Visual and behavioral verification**

At 1440, 768, and 390 px, confirm people remain primary, streams do not create page overflow, keyboard focus pauses a row, tag deep links fill Search, and reduced motion is static.

- [ ] **Step 5: Commit Discover**

```bash
git add index.html
git commit -m "feat: build social discovery experience"
```

---

### Task 8: Search, profiles, favorites, requests, matches, and editing

**Files:**
- Modify: `index.html` sections `Page Components`, `App`, `Self Tests`.

**Interfaces:**
- Produces: `SearchPage`, `PersonProfilePage`, `SwapRequestModal`, `MatchesPage`, `CurrentProfilePage`, `createSwapRequest`, `toggleFavorite`, `updateCurrentProfile`.
- Consumes: router queries, seed lookups, matching/search helpers, state persistence, shared UI.

- [ ] **Step 1: Add state-transition tests**

Verify Save adds/removes only a user ID, request creation produces `{id,targetUserId,offeredSkillId,requestedSkillId,status,createdAt}`, duplicate requests reuse/show Pending instead of adding, and current-user edits never mutate seed constants.

- [ ] **Step 2: Build URL-backed Search**

Inputs update `q`, `level`, `city`, `lang`, and `sort` in the hash. Results use `filterUsers`. Reset Filters returns to `#/search`. Profile → Back restores the exact filter URL and rendered result set.

- [ ] **Step 3: Build dynamic profiles and favorites**

Render complete profile, offered/wanted skills, and data-backed Why You Match. Save/Unsave updates immediately and persists the candidate ID. Invalid IDs render User Not Found.

- [ ] **Step 4: Build the request modal and transition**

Prefill compatible requested/offered skills, allow an optional message only in temporary state, validate IDs, persist the normalized request, announce success, and expose View in Matches. Do not fake a request delay or network call.

- [ ] **Step 5: Build the single Matches route**

Tabs are Suggested, Requests, Connections. Requests contains Incoming and Sent/Pending groups. Connections show disabled Start Chat/Messaging Coming Soon. Every empty group uses EmptyState.

- [ ] **Step 6: Build current-profile editing**

Reuse onboarding fields and skill editors. Save updates only current-user/edited-skill state. Cancel restores the last persisted form snapshot.

- [ ] **Step 7: Verify refresh persistence and commit**

Save a profile, send a request, edit Daniel, refresh, and confirm all three persist. Reset Demo must remove them and leave unrelated localStorage untouched.

```bash
git add index.html
git commit -m "feat: complete search and swap workflows"
```

---

### Task 9: Bilingual coverage, resilience, responsive polish, and accessibility

**Files:**
- Modify: `index.html` all sections.

**Interfaces:**
- Produces: completed `zh`/`en` dictionaries and verified failure/responsive states.
- Consumes: every prior component and helper.

- [ ] **Step 1: Enforce translation completeness**

Remove visible hardcoded interface copy from components. Confirm dictionary key-set test passes, language toggle updates `document.documentElement.lang`, all navigation/validation/request/empty/recovery strings switch, and the choice survives reload.

- [ ] **Step 2: Exercise graceful failures**

Use invalid avatar URLs to verify initials. Insert invalid JSON and version `0` into `skillswap-mvp-v1` and reload. Trigger ErrorBoundary through a self-test-only throwing component. Expected: no blank page, recovery action works, and console contains no uncaught happy-path error.

- [ ] **Step 3: Complete keyboard and dialog checks**

Tab through all primary actions; verify visible focus, logical order, real buttons, form labels, useful alt text, non-color state labels, dialog containment, Escape/overlay close, focus restoration, and `aria-live` success/errors.

- [ ] **Step 4: Complete responsive checks**

Verify 1440 px desktop, 768 px tablet, and 390 px mobile. Confirm consistent destinations, 44 px controls, bottom safe area, stacked forms, readable cards, full-width mobile dialog, and no unintended horizontal page scroll.

- [ ] **Step 5: Run the full self-test suite and commit**

Open `http://localhost:4173/?selftest=1#/`. Expected: every test is PASS and the report contains zero failures.

```bash
git add index.html
git commit -m "fix: harden SkillSwap demo experience"
```

---

### Task 10: End-to-end verification, documentation, push, and GitHub Pages

**Files:**
- Modify: `README.md`
- Verify: `index.html`

**Interfaces:**
- Consumes: the complete MVP.
- Produces: verified `main`, public Pages deployment, final URL.

- [ ] **Step 1: Run the primary judge journey from fresh state**

Reset → Landing → Try Demo → Discover → Alice profile → Why You Match → send request → Matches/Requests → navigate Search/Profile → refresh. Expected: request and user state remain, Back/Forward work, and no step requires explanation.

- [ ] **Step 2: Run the new-user and edge-case journeys**

Complete Signup and all onboarding steps. Verify invalid route, invalid user, empty Search, empty tabs, duplicate request, corrupt storage, old storage, missing avatar, Chinese/English reload, reduced motion, and Reset Demo isolation.

- [ ] **Step 3: Inspect console and source constraints**

Expected: no uncaught happy-path browser errors; one root `index.html` contains all product code; no framework/build artifacts or unpinned product libraries were added.

- [ ] **Step 4: Update README only after local verification**

Document direct local serving, Try Demo, the main journey, single-file architecture, and the final Pages URL. Do not claim the public URL works until it has been opened successfully.

- [ ] **Step 5: Verify the final diff and commit**

Run:

```bash
git status --short
git diff --check
git log --oneline --decorate -12
```

Expected: only intended files changed, no whitespace errors, and the implementation commits are visible.

```bash
git add index.html README.md docs/superpowers/specs/2026-08-16-skillswap-frontend-design.md docs/superpowers/plans/2026-08-16-skillswap-frontend-mvp.md
git commit -m "docs: add SkillSwap demo instructions"
```

Skip the final commit only when there is no new staged change.

- [ ] **Step 6: Push main and enable Pages**

Push `main` to `origin`. Enable GitHub Pages with source branch `main` and path `/`. Wait for deployment completion rather than assuming success.

- [ ] **Step 7: Open and re-verify the public page**

Open the deployed repository Pages URL and repeat Landing → Try Demo → profile → request → refresh. Expected: hash routes, CDN assets, persisted state, and responsive presentation work beneath the repository subpath.

If push, Pages enablement, or deployment is denied, stop at that exact external blocker and report it without claiming completion.
