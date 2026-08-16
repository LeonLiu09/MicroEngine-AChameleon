# SkillSwap 2.0 Single-File Frontend MVP Design

Date: 2026-08-16
Status: Revised from SkillSwap 2.0 project instructions; implementation pending

## 1. Goal and governing priorities

Build a stable, polished, fully interactive hackathon frontend for SkillSwap, a peer-to-peer skill exchange platform. A judge should understand the product without explanation: I can teach something, I want to learn something, SkillSwap finds a compatible person, explains the match, and lets me propose an exchange.

When requirements compete, use this priority order:

1. The main demo journey works reliably.
2. The application never becomes a blank or unrecoverable page.
3. Routing, persisted state, and request state remain internally consistent.
4. The people-first Social Gallery visual system stays coherent.
5. Secondary animation and decorative polish are added only after core behavior is verified.

All frontend implementation lives in one root-level `index.html`. Repository documentation may remain separate, but no product JavaScript, JSX, CSS, data, or component code is split into another file.

## 2. Confirmed product decisions

- UI brand: **SkillSwap**; project generation: **SkillSwap 2.0**.
- Visual direction: **B — Social Gallery**, adapted from the supplied pink minimalist Pinterest reference.
- Chinese is the initial language; a persistent top-right control switches the complete interface to English.
- The complete documented flow is interactive rather than a static screen collection.
- Desktop and tablet use top navigation; mobile uses bottom navigation.
- Discover includes complementary swaps and same-level Peer Buddies.
- A three-person SkillLoop uses deterministic mock data and clearly identifies itself as a demo result.
- User-created state persists in `localStorage` and can be reset safely.
- CDN libraries, one web font, icons, and remote avatar images are allowed with readable fallbacks.
- Mock profiles may include minors, but reveal only city-level location and never school, precise location, contact information, or direct stranger messaging.
- Final delivery goes directly to `main`, then to GitHub Pages.

## 3. Delivery and runtime architecture

### 3.1 Single-file structure

The file uses this order and clear section comments:

1. Static fallback markup inside `#root`.
2. One `<style>` block containing design tokens, reset/base styles, layout, shared components, page styles, and responsive rules.
3. Pinned React 18.3.x CDN script.
4. Pinned ReactDOM 18.3.x CDN script.
5. Pinned Babel Standalone CDN script.
6. One `<script type="text/babel">` containing constants, i18n, seed data, state helpers, router, matching logic, utilities, shared components, page components, `ErrorBoundary`, `App`, and the React 18 `createRoot` entry.

There is no Next.js, React Router, TypeScript build, Tailwind build, Vite, Webpack, backend runtime, or package installation.

### 3.2 Failure-safe boot

Before React mounts, `#root` contains readable static HTML with the SkillSwap name and a bilingual connection/reload message. A React, ReactDOM, Babel, font, icon, or avatar CDN failure must not produce a blank page.

An inline bootstrap check retains or restores the fallback when required globals are unavailable. A class-based React `ErrorBoundary` surrounds the application and shows:

- A friendly bilingual “Something went wrong” message.
- Reload.
- Reset Demo, which removes only the SkillSwap key.

System sans-serif fonts, Unicode/text icon alternatives, and avatar initials keep all primary navigation understandable when presentation assets fail.

### 3.3 Internal component boundaries

Reusable components include Button, Input, Avatar, SkillTag, SkillEditor, SkillLevelSelector, UserCard, MatchBadge, MatchReason, Navigation, MobileNavigation, SearchBar, Modal, Progress, EmptyState, ProfileHeader, ProfileSkillSection, and ErrorBoundary.

Do not over-componentize. Extract a unit only when it is reused or when it separates meaningful state/behavior. Matching, persistence, routing, query parsing, validation, and lookups stay outside JSX in small pure helpers.

## 4. Hash router and route guards

### 4.1 Routes

Use `window.location.hash` as the only navigation source of truth:

- `#/` — Landing
- `#/login` — Demo login
- `#/signup` — Account creation
- `#/onboarding/profile`
- `#/onboarding/share`
- `#/onboarding/learn`
- `#/onboarding/complete`
- `#/discover`
- `#/search`
- `#/matches`
- `#/profile`
- `#/people/:id`

`navigate(path)` writes the hash. `hashchange` updates the rendered route. Do not mix hash routing with `pushState` or an independent current-page state. Browser Back and Forward must restore routes and URL-backed search filters.

Unknown routes render a friendly Page Not Found state with Back to SkillSwap. Unknown person IDs render User Not Found with Back to Discover; undefined user data is never passed into profile components.

### 4.2 Centralized three-state guards

The router resolves one of three application states:

1. Unauthenticated.
2. Authenticated with incomplete onboarding.
3. Authenticated with complete onboarding.

Guard behavior:

- Public routes are available without authentication. An already completed demo user who opens Login or Signup is redirected to Discover.
- Onboarding routes redirect unauthenticated users to Signup and completed users to Discover.
- Application routes redirect unauthenticated users to Login.
- Application routes redirect incomplete users to their saved onboarding step.
- Guards are centralized, return one final route, and cannot redirect recursively.

## 5. Seed data, user state, and persistence

### 5.1 Immutable seed data

Central constants hold skills, users, match inputs, incoming requests, connections, and SkillLoop data. Seed records use stable IDs and are treated as immutable. Lookup helpers include `getUserById()` and `getSkillById()`.

At least six realistic users cover Photography, Chemistry, Python, Guitar, Badminton, Cooking, English, Drawing, Fitness, Video Editing, Arduino, and Writing. Profiles include bilingual bio/skill descriptions, city, language, level, and enough overlap to produce explainable results.

The full mock database is never duplicated into storage.

### 5.2 Versioned local state

Use the exact key `skillswap-mvp-v1` and schema version `1`. The stored object contains only changed/demo-user state:

```js
{
  version: 1,
  language: "zh",
  isAuthenticated: false,
  currentUser: null,
  onboardingCompleted: false,
  onboardingStep: "profile",
  favorites: [],
  sentRequests: [],
  connections: [],
  editedSkills: []
}
```

Minor schema refinements are allowed only when they preserve the same separation between seed data and changed state.

`loadState()` uses `try/catch`, validates the version and expected value types, whitelists usable properties, and merges them with `createInitialState()`. Missing, corrupt, inaccessible, or old-version storage falls back safely. `saveState()` also uses `try/catch`; failure may warn in the console but never crashes the UI.

Never persist an open modal, hover state, temporary validation message, form error, or raw search keyword.

### 5.3 Reset Demo

Reset requires explicit confirmation, calls only `localStorage.removeItem(STORAGE_KEY)`, reinitializes state, and returns to `#/`. It never calls `localStorage.clear()` or touches unrelated origin data.

### 5.4 Daniel demo account

Landing and Login provide a prominent Try Demo action. `loginAsDemoUser()` constructs a rich Daniel state from seed data:

- Completed profile and onboarding.
- Skills offered and wanted.
- Seeded favorites.
- One useful pending/request example.
- One connection.
- Enough information for complementary, peer, and SkillLoop displays.

It navigates directly to `#/discover`. Judges do not need to create an account to see the primary experience.

## 6. Deterministic matching model

Matching is a set of pure frontend functions over the current user and seed users. It is deterministic, explainable, and intentionally not described as AI.

### 6.1 Complementary swaps

A candidate qualifies when they teach at least one skill the current user wants. Scoring uses fixed, capped factors:

- Candidate teaches a wanted skill: required base score.
- Current user teaches a skill the candidate wants: strong two-way bonus.
- Compatible skill level: smaller bonus.
- Shared language: smaller bonus.
- Shared category/interest: smaller bonus.

Tie-breaking is stable by user ID. The displayed reason is generated from the exact factors that contributed to the score and names both skill directions for a two-way exchange.

### 6.2 Peer Buddies

Peer matching requires at least one shared skill at the same effective level and at least one shared language. It explains the shared skill/level and presents the result separately from complementary exchange cards.

### 6.3 SkillLoop

The demo displays one seeded three-person directed loop:

- Alice teaches Photography to Daniel.
- Daniel teaches Chemistry to Bob.
- Bob teaches Cooking to Alice.

The visualization labels every directed edge, shared demo time, and participant. It never claims to have queried a server or live graph model.

## 7. Visual system and motion

### 7.1 Social Gallery direction

The supplied reference contributes dusty-pink atmosphere, glass panels, rounded white surfaces, black contrast blocks, oversize geometric type, and pill controls. SkillSwap adapts these into an original people-first gallery rather than shipping the Pinterest image itself.

Primary tokens:

- Ink `#181818`
- Paper `#FFF9FA`
- Deep rose `#B85F7D`
- Active rose `#D97091`
- Soft rose `#EBA0B5`
- Blush `#F7DCE3`
- Glass `rgba(255,255,255,0.58)`
- Glass border `rgba(255,255,255,0.64)`
- Muted text `rgba(24,24,24,0.64)`
- Accessible dark red error and dark green success, always paired with text/icon

Category accents stay muted and consistent: Academic rose, Technology lavender, Creative peach, Sports coral, Lifestyle sand. Color is never the only category or status signal.

Geometry:

- Hero/page radius: 28–32 px.
- Card radius: 20–24 px.
- Input radius: 16–18 px.
- Pill radius: fully rounded.
- Spacing: 4, 8, 12, 16, 24, 32, 48, 72 px.
- Broad, low-opacity rose-black glass shadows.
- One geometric sans-serif web font with Inter/system fallbacks.

### 7.2 Flowing skill tags

Landing and Discover use two seamless horizontal skill-tag rows moving in opposite directions at 26 and 32 seconds per cycle. Hover and keyboard focus pause the relevant row. On Discover, tags are real buttons that navigate to `#/search?q=<skill>`.

Mobile slows the perceived motion. Under `prefers-reduced-motion: reduce`, rows stop and become a static wrapping list. Other transitions remain 180–280 ms and restrained. No parallax, automatic carousel, or competing infinite animation is added.

Motion is P2 polish: it is disabled or simplified rather than delaying or destabilizing P0 flows.

## 8. Pages and user flows

### 8.1 Landing, Login, and Signup

Landing shows SkillSwap, bilingual slogan/value proposition, Login, Create Account, Try Demo, animated skill streams, and a compact Social Gallery preview.

Login has Email and Password plus Try Demo. Mock form submission restores Daniel and opens Discover. Signup validates email format, minimum eight-character password, and matching confirmation, then establishes local demo authentication and enters onboarding. Credentials are never transmitted and passwords are not retained as reusable credentials.

### 8.2 Three-step onboarding

1. Profile: mock avatar, nickname, bio, city-level location, languages, age.
2. Share: one or more skills with name, searchable category, description, and Beginner/Intermediate/Advanced level.
3. Learn: one or more goals with name, category, description, and Complete Beginner/Beginner/Intermediate level.

Every repeatable editor adds/removes items with stable local IDs. Required fields show friendly immediate validation. Duplicate skills merge or show a clear warning. The final required offered/wanted skill cannot be removed without replacement. Completion summarizes both lists before Find Skill Partners routes to Discover.

### 8.3 Discover

Discover prioritizes:

- Personalized greeting and prominent Search.
- Learning-goal tags and flowing skill rows.
- Recommended complementary swaps.
- Peer Buddies.
- Featured three-person SkillLoop.

Cards lead with avatar, name, personality, and skills; score and supporting data come second. Cards provide Save and View Profile. Every recommendation uses realistic seed content and an explanation based on deterministic matching factors.

### 8.4 Search with URL state

Search parses lightweight hash query parameters such as:

- `#/search?q=python`
- `#/search?q=python&level=beginner&city=tianjin&lang=zh&sort=best`

Search filters by teachable skill, level, city, and language, with Best Match sorting. Opening a profile and pressing Back restores the prior search because the query lives in the hash. No result shows a shared EmptyState with Reset Filters.

### 8.5 Other-user profile and favorites

The profile shows avatar, nickname, city, languages, bio, offered skills, wanted skills, and a visual Why You Match explanation. Save/Unsave writes only the user ID to `favorites` and updates immediately.

### 8.6 Swap request modal and state

Send Skill Exchange Request opens a reusable dialog with requested skill, offered skill, and optional message. A stored request contains:

```js
{
  id,
  targetUserId,
  offeredSkillId,
  requestedSkillId,
  status,
  createdAt
}
```

Sending updates the UI immediately, persists locally, announces success, and prevents an accidental duplicate exchange request. No fake network call is shown.

Modal requirements: real close button, overlay click, Escape close, `role="dialog"`, `aria-modal="true"`, focus containment, and focus restoration.

### 8.7 Matches

Use one `#/matches` route with tabs:

- Suggested.
- Requests, containing Incoming and Sent/Pending sections.
- Connections.

Do not add separate Favorites, Requests, or Connections routes. Empty arrays always render a meaningful EmptyState. Connected users show a disabled Start Chat with Messaging Coming Soon.

### 8.8 Current-user profile

Profile shows Daniel or the newly onboarded user. Edit Profile and Edit Skills reuse onboarding controls and persist only changed current-user state.

## 9. Internationalization, responsive behavior, and safety

### 9.1 Internationalization

Central `zh` and `en` dictionaries contain every interface-owned string: navigation, buttons, labels, validation, empty states, errors, modal content, matching explanations, request states, and safety notices. Seeded bios and descriptions provide language variants; user-entered content remains unchanged.

The language control updates `document.documentElement.lang`, visible labels, accessible labels, and storage immediately.

### 9.2 Responsive behavior

- Desktop at 1024 px and above: top navigation, up to four gallery columns, editorial spacing.
- Tablet from 768–1023 px: top navigation, two-column cards, wrapped controls.
- Mobile below 768 px: bottom navigation, one-column primary cards, compact gallery only when readable, stacked forms, full-width dialog, safe-area padding.

Primary destinations—Discover, Search, Matches, Profile—exist on every viewport. Touch targets are at least 44 px and no core action depends on hover.

### 9.3 Accessibility and youth safety

- Semantic landmarks, headings, forms, labels, lists, links, and real buttons.
- Useful image alt text and visible keyboard focus.
- `aria-live` for validation, storage recovery notice, and request success.
- WCAG AA-oriented contrast and non-color state labels.
- Reduced-motion handling for every nonessential animation.
- City only; no school, address, coordinates, phone, public email, or external handles.
- Minor profiles cannot be contacted through real private chat; chat remains unavailable.
- Registration and storage are clearly local demo behavior.

## 10. Graceful failure behavior

- Missing React/ReactDOM/Babel: readable static fallback remains.
- React render failure: ErrorBoundary recovery screen.
- Missing font: system sans-serif.
- Missing icon: visible text or Unicode fallback.
- Missing/broken avatar: gradient plus initials through image `onError`.
- Missing/invalid form value: inline React-state error.
- Invalid/old/inaccessible storage: sanitized initial state.
- Save failure: nonfatal notice; UI remains operable.
- Unknown route: Page Not Found.
- Unknown person: User Not Found.
- Empty dataset/list: shared EmptyState.
- Duplicate request: existing Pending state.

## 11. Implementation priorities

### P0 — must work first

Application boot, React render, fallback HTML, hash routing, Back/Forward, centralized route guards, storage recovery, Try Demo, Discover, dynamic profile, request sending, Matches state, Reset Demo, mobile usability, and no blank screens.

### P1 — strongly recommended

ErrorBoundary, URL-backed Search, favorites, deterministic explanations, Requests/Connections tabs, empty states, avatar fallbacks, complete bilingual coverage, and responsive polish.

### P2 — polish

Flowing skill tags, micro-interactions, success celebration, and secondary accessibility refinements. P2 never delays a broken P0 journey.

## 12. Verification and acceptance

Fresh verification must cover:

1. Fresh first visit shows Landing and never a blank screen.
2. Try Demo logs in Daniel and reaches Discover.
3. Signup and all three onboarding steps complete successfully.
4. Route guards handle all three authentication/onboarding states without loops.
5. Back and Forward work between Discover, Search, and profiles.
6. `#/people/:id` works; an invalid ID produces User Not Found without errors.
7. Search query and filters restore from the hash after profile navigation.
8. Deterministic matches and their explanations reflect seed/current-user data.
9. Sending a request updates Matches, survives refresh, and cannot duplicate accidentally.
10. Favorites survive refresh and store IDs rather than user objects.
11. Connections and every empty list render correctly.
12. Reset Demo removes only `skillswap-mvp-v1` and returns to Landing.
13. Missing, invalid JSON, wrong-version, and inaccessible storage do not crash the app.
14. Avatar failures show initials; icon/font failures retain understandable UI.
15. Static fallback and ErrorBoundary recovery paths exist and are structurally checked.
16. Chinese/English switches every view and persists after reload.
17. Flowing tags loop, pause, deep-link to Search, and stop under reduced motion.
18. Modal closes by button, overlay, and Escape and restores focus.
19. Desktop 1440 px, tablet 768 px, and mobile 390 px remain usable.
20. The happy path has no uncaught console error.
21. The root `index.html` runs from a local static server and the GitHub Pages repository subpath.

The primary judge journey has the highest acceptance weight:

Landing → Try Demo → Discover → inspect a good match → profile → understand complementary skills → send request → Matches state → navigate → refresh → state remains.

## 13. GitHub Pages and delivery

All asset paths and hash routes must work beneath the repository Pages subpath. The project needs no Node runtime after authoring and no server rewrite.

After local verification:

1. Review the exact diff and staged files.
2. Commit directly to `main` with a descriptive message.
3. Push `main` to `origin`.
4. Enable GitHub Pages from the `main` branch repository root.
5. Wait for deployment, open the public URL, and repeat the primary journey.

If Pages permissions or deployment fail, stop at that external decision point and report the exact blocker rather than claiming completion.

## 14. Explicit exclusions

Do not add backend servers, databases, REST/GraphQL, Firebase, Supabase, OAuth, real authentication, real chat, WebSockets, email systems, notifications, video calls, payments, AI APIs, machine learning, admin dashboards, social feeds, course marketplace, React Router, Redux, Zustand, MobX, React Query, or any build system.

The current goal is to finish the frontend MVP extremely well, not to simulate production infrastructure.
