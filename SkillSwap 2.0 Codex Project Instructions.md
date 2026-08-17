# SkillSwap 2.0 Frontend MVP — Project Instructions

You are working on **SkillSwap 2.0**, a hackathon frontend MVP for a peer-to-peer skill exchange platform.

Your role is to act as the primary frontend engineer for this project.

Before modifying anything, inspect the existing repository and understand the current implementation. Preserve working code and existing visual design where possible. Do not blindly replace existing work.

The immediate goal is to produce a **stable, polished, fully interactive frontend demo** suitable for a hackathon presentation.

---

# 1. Project scope

This phase is **frontend only**.

Do NOT add:

- Backend servers
- Databases
- Authentication services
- REST APIs
- GraphQL
- Firebase
- Supabase
- Next.js
- NestJS
- Express
- Node.js server code
- TypeScript build systems
- Tailwind build tooling
- Vite
- Webpack
- React Router
- Other frameworks or unnecessary dependencies

Do not redesign the architecture unless explicitly requested.

All backend-like behavior must currently be simulated using:

- Static mock data
- React state
- `localStorage`

The objective is not production infrastructure.

The objective is a convincing, reliable frontend MVP.

---

# 2. Delivery constraint

The entire application must remain deliverable as:

```text
index.html
```

The project must work as a **single-file React application**.

Do NOT split the application into multiple JavaScript, JSX, CSS, or component files unless explicitly instructed later.

Inside `index.html`, use approximately this structure:

```text
index.html

Static fallback HTML

<style>
    Design tokens
    Global styles
    Components
    Pages
    Responsive styles
</style>

React CDN
ReactDOM CDN
Babel Standalone CDN

<script type="text/babel">
    Constants
    i18n
    Mock data
    State helpers
    Router
    Matching logic
    Utilities
    Shared components
    Page components
    ErrorBoundary
    App
    React render entry
</script>
```

Maintain clear section comments so that the large single file stays readable.

---

# 3. React runtime

Use fixed/pinned CDN versions.

Use:

- React 18.3.x
- ReactDOM 18.3.x
- A pinned version of Babel Standalone

JSX is compiled directly in the browser using:

```html
<script type="text/babel">
```

Do not introduce a build process.

Use the React 18 `createRoot` API.

---

# 4. Failure-safe initial rendering

The page must NEVER appear completely blank simply because an external resource failed.

Place readable static fallback HTML directly inside:

```html
<div id="root">
```

before React starts.

For example, the fallback can contain:

```text
SkillSwap

Unable to load the application.
Please check your internet connection and refresh the page.
```

When React successfully mounts, React should replace this fallback.

Therefore:

- React CDN failure must not create a blank page.
- ReactDOM failure must not create a blank page.
- Babel failure must not create a blank page.
- Web font failure must not make text unreadable.
- Icon failure must not prevent navigation.
- Avatar image failure must not break user cards.

Also implement a React `ErrorBoundary` around the application.

If a React rendering error occurs, show a friendly recovery UI with actions such as:

```text
Something went wrong.

Reload
Reset Demo
```

---

# 5. Styling

All CSS lives inside one `<style>` block.

Organize CSS roughly as:

```text
1. Design tokens
2. Reset / base styles
3. Layout
4. Shared components
5. Page-specific styles
6. Responsive rules
```

Use CSS custom properties for important design values, for example:

```css
:root {
    --color-primary: ...;
    --color-background: ...;
    --color-text: ...;
    --radius-sm: ...;
    --radius-md: ...;
    --radius-lg: ...;
    --shadow-card: ...;
    --max-width: ...;
}
```

The application must be responsive for:

- Desktop
- Tablet
- Mobile

Do not require Tailwind.

Preserve the established visual identity if one already exists.

---

# 6. Fonts

Use one geometric sans-serif web font.

Always provide system fallbacks similar to:

```css
font-family:
    "ProjectFont",
    Inter,
    system-ui,
    -apple-system,
    BlinkMacSystemFont,
    "Segoe UI",
    sans-serif;
```

If using `@font-face`, use an appropriate `font-display` strategy so that readable fallback text appears immediately.

Font failure must never block readable content.

---

# 7. Icons

Use one fixed-version browser-compatible icon source.

Do not add a heavy icon framework.

Where an icon is important to functionality, provide a text or Unicode fallback.

The application must remain usable if the icon CDN fails.

---

# 8. Avatar behavior

Mock users may use remote avatar images.

Every avatar must have a fallback.

If:

- the avatar URL is missing, or
- the remote image fails to load,

display:

```text
gradient background
+
user initials
```

Example:

```text
Daniel Kim
→ DK
```

Use the image `onError` event to switch to the fallback.

Avatar failures must never produce broken-image UI.

---

# 9. Internal code organization

Within the `<script type="text/babel">` block, keep sections in this order:

## 9.1 Constants

Examples:

```js
STORAGE_KEY
STATE_VERSION
skill levels
supported languages
route definitions
```

## 9.2 Translation dictionaries

All supported UI translations should live in centralized translation dictionaries.

Do not scatter translated strings randomly throughout components.

## 9.3 Mock / seed data

Include mock data for:

- Skills
- Users
- Match results
- Swap requests
- Connections
- SkillLoop data

Mock data represents application seed data and should remain in JavaScript constants.

Do NOT duplicate the full mock database into localStorage.

## 9.4 State and helper functions

Examples:

```text
createInitialState()
loadState()
saveState()
resetDemo()
```

## 9.5 Router

Examples:

```text
parseRoute()
navigate()
routeGuard()
```

## 9.6 Matching logic

Keep matching calculations in pure helper functions instead of embedding large algorithms directly inside JSX.

## 9.7 Utilities

Examples:

```text
getInitials()
skill formatting
query parsing
validation
ID lookup
```

## 9.8 Reusable UI components

## 9.9 Page components

## 9.10 ErrorBoundary

## 9.11 App

## 9.12 React createRoot entry point

---

# 10. Reusable components

The project may use the following reusable components:

```text
Button
Input
Avatar
SkillTag
SkillEditor
SkillLevelSelector
UserCard
MatchBadge
Navigation
MobileNavigation
SearchBar
Modal
Progress
EmptyState
ProfileHeader
ProfileSkillSection
ErrorBoundary
```

Do not over-componentize the application.

Only extract small components when doing so materially improves reuse or clarity.

If something is used only once and is very small, keeping it inside its parent component is acceptable.

---

# 11. Hash Router

Do NOT install React Router.

Implement a lightweight custom Hash Router.

Routes:

```text
#/                         Landing Page

#/login                    Demo login

#/signup                   Signup

#/onboarding/profile       Onboarding: profile
#/onboarding/share         Onboarding: skills user can teach
#/onboarding/learn         Onboarding: skills user wants to learn
#/onboarding/complete      Onboarding complete

#/discover                 Discover users / skills
#/search                   Search
#/matches                  Matches
#/profile                  Current user profile

#/people/:id               Other user's profile
```

Navigation should happen through a single helper, conceptually:

```js
function navigate(path) {
    window.location.hash = path;
}
```

Listen for:

```text
hashchange
```

and derive the current React route from:

```text
window.location.hash
```

Do NOT maintain a second independent page-navigation state.

Do NOT mix:

```text
location.hash
pushState
custom currentPage state
```

as competing routing systems.

Browser:

```text
Back
Forward
```

buttons must work correctly.

---

# 12. Dynamic profile routes

Support:

```text
#/people/:id
```

The router must extract the user ID.

Example:

```text
#/people/sarah-01
```

If the requested user exists:

```text
render that user's profile
```

If the ID does not exist:

render an `EmptyState` such as:

```text
User not found

Back to Discover
```

Never pass `undefined` user data into profile components.

---

# 13. Route guards

Separate routes into three states:

```text
Unauthenticated

Authenticated but onboarding incomplete

Authenticated and onboarding complete
```

Expected behavior:

### Public routes

```text
#/
#/login
#/signup
```

Accessible without authentication.

### Onboarding routes

```text
#/onboarding/*
```

If not logged in:

```text
redirect to signup or login
```

If onboarding has already been completed:

```text
redirect to discover
```

### Application routes

```text
#/discover
#/search
#/matches
#/profile
#/people/:id
```

If not authenticated:

```text
redirect to login
```

If authenticated but onboarding is incomplete:

```text
redirect to the appropriate onboarding step
```

Otherwise allow access.

Avoid redirect loops.

Route guards must be centralized rather than duplicated across every page.

---

# 14. Local application state

Use this localStorage key exactly:

```text
skillswap-mvp-v1
```

Store a versioned state object.

Conceptually:

```js
{
    version: 1,

    language: "en",

    isAuthenticated: false,

    currentUser: null,

    onboardingCompleted: false,

    favorites: [],

    sentRequests: [],

    connections: [],

    editedSkills: []
}
```

The exact schema may evolve slightly if necessary, but keep it simple and explicit.

---

# 15. localStorage robustness

Never assume localStorage data is valid.

`loadState()` must safely handle:

- Missing storage
- Invalid JSON
- Old schema versions
- Unexpected properties
- localStorage access exceptions

Use `try/catch`.

If recovery fails:

```text
return createInitialState()
```

Do not crash the application.

When restoring state, merge appropriate stored values with safe defaults.

Similarly, `saveState()` must use `try/catch`.

A storage failure may produce a console warning, but must NOT crash the UI.

---

# 16. Seed data versus user state

Keep static mock data in JavaScript constants.

For example:

```js
const MOCK_USERS = [...]
const MOCK_SKILLS = [...]
```

Do not persist all mock users into localStorage.

The distinction is:

```text
Seed Data
→ source code

User-created / user-modified state
→ localStorage
```

localStorage should primarily contain things that changed during the demo.

For example:

```text
current user
language
favorites
sent requests
connections
edited skills
onboarding state
authentication state
```

---

# 17. Reset Demo

Implement:

```text
Reset Demo
```

Require user confirmation before resetting.

Reset Demo must ONLY remove:

```text
skillswap-mvp-v1
```

Use conceptually:

```js
localStorage.removeItem(STORAGE_KEY);
```

Do NOT call:

```js
localStorage.clear();
```

After resetting:

```text
return to #/
reload/reinitialize application state
```

---

# 18. Temporary state

The following must NOT be persisted in localStorage:

```text
currently open modal
temporary validation messages
temporary UI hover state
temporary form errors
```

Use React component state for them.

---

# 19. Search state

Search keywords do not need persistent storage.

Prefer representing useful search state in the hash URL where appropriate.

Example:

```text
#/search?q=python
```

Optional filters could become:

```text
#/search?q=python&level=beginner
```

This allows:

```text
Search
→ open a profile
→ browser Back
→ previous search is restored
```

without storing the query permanently.

Keep query parsing lightweight.

---

# 20. Demo user

Provide a one-click demo account.

The predefined demo user is:

```text
Daniel
```

Implement a clear action such as:

```js
loginAsDemoUser()
```

The demo account should restore a rich preconfigured state suitable for presentation.

Daniel should have:

```text
completed profile
skills he can teach
skills he wants to learn
onboarding completed
some favorites
sample matches
sample request / connection data where useful
```

After selecting:

```text
Try Demo
```

or equivalent,

the user should enter:

```text
#/discover
```

without needing to complete onboarding.

This flow exists so judges can immediately experience the main product.

---

# 21. Main product flow

The most important demo journey is:

```text
Landing
↓
Try Demo / Daniel login
↓
Discover
↓
View another user's profile
↓
Understand why the user is a match
↓
Send Skill Swap request
↓
Matches / Requests
↓
Connection state
```

This flow has higher priority than secondary features.

When deciding what to polish first, prioritize this journey.

---

# 22. Discover

The Discover experience should emphasize:

```text
people
skills
compatibility
matching reasons
```

User cards may contain:

```text
avatar
name
short bio
skills offered
skills wanted
match percentage / level
match reason
Save action
View Profile
```

Use realistic mock data.

Avoid cards containing meaningless placeholder text.

---

# 23. Matching

This is not meant to be a sophisticated AI recommendation backend.

Implement deterministic frontend matching logic using mock profile data.

Possible factors include:

```text
User A wants a skill User B can teach

User B wants a skill User A can teach

skill-level compatibility

shared interests

availability compatibility
```

Keep the algorithm:

```text
simple
deterministic
explainable
easy to demo
```

A match should not only display a score.

Where useful, explain WHY the users matched.

Example:

```text
92% Match

Daniel wants to learn Photography.
Sarah can teach Photography.

Sarah wants to learn Python.
Daniel can teach Python.
```

The explanation is more important than mathematical sophistication.

---

# 24. Matches page

Avoid creating unnecessary separate top-level routes.

The main Matches page may contain tabs such as:

```text
Suggested
Requests
Connections
```

This allows swap-related state to remain centralized.

Do not create:

```text
#/favorites
#/requests
#/connections
```

unless explicitly requested later.

---

# 25. Favorites

Users may save another user.

Favorites should persist in:

```text
skillswap-mvp-v1
```

Store IDs rather than full duplicate user objects where practical.

Example:

```js
favorites: ["user-sarah", "user-alex"]
```

The UI should immediately reflect save / unsave changes.

---

# 26. Swap requests

Sending a Skill Swap request must work entirely in the frontend.

A request should contain enough information to render later.

For example:

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

Do not pretend to send a real network request.

Update the UI immediately and persist it locally.

Avoid duplicate requests to the same exchange unless intentionally allowed.

---

# 27. Modal behavior

Reusable `Modal` components should support at least:

```text
Close button
Click outside / overlay to close
Escape key to close
```

Use appropriate dialog semantics such as:

```html
role="dialog"
aria-modal="true"
```

Do not introduce a third-party modal library.

---

# 28. Forms

Provide visible validation for important onboarding and swap-request forms.

Validation should be:

```text
simple
friendly
immediate
```

Examples:

```text
required name
at least one skill to teach
at least one desired skill
valid skill level
```

Validation messages should stay in React state and should not be persisted.

Do not over-engineer form architecture.

---

# 29. Responsive navigation

Desktop and mobile navigation may use different components:

```text
Navigation
MobileNavigation
```

The core available destinations should remain consistent.

Primary application destinations are:

```text
Discover
Search
Matches
Profile
```

Do not hide important functionality only on desktop.

---

# 30. Empty states

Pages should gracefully handle empty arrays.

Use the shared `EmptyState` component for cases such as:

```text
No search results
No matches yet
No requests
No connections
User not found
No saved people
```

Never leave an unexplained blank section.

---

# 31. Accessibility baseline

This is a hackathon MVP, not a full accessibility certification project, but basic semantics matter.

At minimum:

- Buttons should be actual `<button>` elements.
- Inputs should have labels or appropriate `aria-label`.
- Images should have useful `alt`.
- Interactive cards should not rely only on hover.
- Keyboard focus should remain visible.
- Modal semantics should be appropriate.
- Color should not be the only indicator of state.

Do not spend disproportionate time implementing advanced accessibility infrastructure.

---

# 32. Performance philosophy

This is a small static frontend application.

Prefer:

```text
simple code
small helpers
small components
deterministic data
minimal dependencies
```

over premature optimization.

Do not introduce:

```text
Redux
Zustand
MobX
React Query
complex state libraries
complex memoization
```

The application should be understandable from one HTML file.

---

# 33. Data consistency

Prefer IDs when connecting data.

Example:

```text
user ID
skill ID
request ID
```

Avoid repeatedly copying full objects into multiple state arrays when IDs are sufficient.

Centralize lookup helpers where useful.

Example:

```js
getUserById()
getSkillById()
```

Avoid hidden mutation of shared mock data.

Treat seed constants as immutable.

---

# 34. Error handling philosophy

The demo should fail gracefully.

Where reasonable:

```text
bad avatar
→ initials

bad route
→ Not Found / Landing

unknown user
→ EmptyState

corrupt storage
→ initial state

missing font
→ system font

missing icon
→ fallback character

React render error
→ ErrorBoundary

React/Babel CDN unavailable
→ static fallback HTML
```

A recoverable failure must not become a blank screen.

---

# 35. Unknown routes

If the hash does not match a known route:

prefer a graceful fallback such as:

```text
Page not found

Back to SkillSwap
```

or safely redirect to:

```text
#/
```

Do not throw an exception.

---

# 36. Development priorities

Prioritize work in this order:

## P0 — Must work

```text
Application boot
React rendering
Hash routing
Browser Back / Forward
Route guards
localStorage recovery
Demo login
Discover
Profiles
Send swap request
Matches
Reset Demo
Mobile usability
No blank screens
```

## P1 — Strongly recommended

```text
ErrorBoundary
Search URL state
Favorites
Match explanations
Requests / Connections tabs
Good empty states
Avatar fallbacks
Responsive polish
```

## P2 — Polish

```text
Animations
micro-interactions
extra accessibility polish
small visual details
```

Do not spend substantial time on P2 while any P0 feature is broken.

---

# 37. Coding style

Because everything lives in one file:

- Prefer clear names over clever abstractions.
- Keep functions small.
- Keep matching and state logic outside JSX where practical.
- Avoid deeply nested ternaries.
- Avoid giant components where natural boundaries exist.
- Add section comments.
- Keep constants centralized.
- Avoid global mutable variables.
- Do not duplicate logic across pages.
- Do not introduce unnecessary dependencies.

Readable code is more important than creating an enterprise architecture.

---

# 38. Existing code

If the repository already contains implementation:

1. Inspect it first.
2. Understand what already works.
3. Preserve good existing UI and logic.
4. Modify incrementally.
5. Do not rewrite the entire file merely because you prefer another architecture.
6. Do not change working behavior unrelated to the current task.
7. If an existing implementation conflicts with these project constraints, update it toward these constraints.

Before adding a library or architectural change, first determine whether native browser APIs or existing project code already solve the problem.

---

# 39. Verification

After implementing changes, verify the important frontend journeys yourself.

Check at minimum:

```text
fresh first visit

Landing renders

Daniel Demo Login works

Discover renders mock users

user profile navigation works

#/people/:id works

invalid user ID does not crash

Search works

browser Back works

browser Forward works

send request works

request remains after refresh

favorites remain after refresh

Reset Demo resets only SkillSwap state

invalid localStorage JSON does not crash

mobile layout remains usable
```

Also check the browser console for obvious runtime errors.

Do not consider the task complete merely because the code looks correct.

---

# 40. GitHub Pages

The frontend is intended to be deployable directly to GitHub Pages.

Therefore:

```text
no server-side routing
no server-only APIs
no Node runtime requirement
no backend dependency
```

Hash routing exists specifically so refreshing/deep-linking does not require server URL rewriting.

Keep all resource paths compatible with static hosting.

---

# 41. Product philosophy

SkillSwap should feel like a real product rather than a collection of disconnected demo screens.

The experience should communicate:

> I have skills I can teach.
>
> I have skills I want to learn.
>
> SkillSwap finds people whose needs complement mine.
>
> It explains why we match.
>
> I can propose an exchange.

Every major UI decision should reinforce that loop.

---

# 42. Scope discipline

This is especially important.

Do NOT spontaneously add:

```text
real chat
video calls
payments
AI API integrations
backend authentication
database architecture
email systems
notification servers
WebSockets
complex recommendation models
admin dashboards
social feeds
```

unless explicitly requested.

Mocking a future feature visually is acceptable if required for the demo, but do not build unnecessary infrastructure.

The current goal is:

**Finish the frontend MVP extremely well.**

---

# 43. When making changes

When I give you a new task:

1. Inspect the relevant existing code.
2. Determine how it fits this architecture.
3. Make reasonable assumptions where details are minor.
4. Implement the feature completely.
5. Integrate it with existing state and routing.
6. Test the affected flow.
7. Fix issues you discover that directly affect the requested feature.
8. Keep changes within project scope.

Do not stop after merely describing what you would implement.

Do not ask me to make routine implementation decisions that can reasonably be inferred from this specification.

If a request conflicts with this document, explicitly identify the conflict before changing the fundamental architecture.

---

# 44. Current project goal

For now, focus exclusively on completing the **frontend demo**.

A successful result should allow a hackathon judge to open the site and experience this sequence without explanation:

```text
Landing
→ Try Demo
→ Discover
→ inspect a good match
→ open profile
→ understand complementary skills
→ send Skill Swap request
→ see request / match state
→ navigate around
→ refresh
→ state remains intact
```

The experience should feel intentional, responsive, coherent, and stable.

That is the definition of success for the current phase.